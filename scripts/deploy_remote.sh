#!/usr/bin/env bash

# Runs on the VPS after the release has been uploaded.
set -euo pipefail
release="$1"
source "$release/scripts/deploy_support.sh"
previous_release="$(readlink -f /opt/eden/current 2>/dev/null || true)"
previous_nginx="/etc/nginx/sites-available/eden-api"
previous_unit="/etc/systemd/system/eden-api.service"
rollback_dir="$(mktemp -d)"
snapshot_path() {
  local path="$1"
  local snapshot_name="$2"
  if [[ -e "$path" || -L "$path" ]]; then
    cp -a -- "$path" "$rollback_dir/$snapshot_name"
    : >"$rollback_dir/$snapshot_name.present"
  else
    : >"$rollback_dir/$snapshot_name.absent"
  fi
}
restore_snapshot() {
  local path="$1"
  local snapshot_name="$2"
  rm -f -- "$path"
  if [[ -f "$rollback_dir/$snapshot_name.present" ]]; then
    cp -a -- "$rollback_dir/$snapshot_name" "$path"
  fi
}
pilot_report_service="/etc/systemd/system/eden-phase2-pilot-report.service"
pilot_report_timer="/etc/systemd/system/eden-phase2-pilot-report.timer"
preserve_fail_closed=0
deployment_complete=0
snapshot_path /opt/eden/current current_release
snapshot_path "$previous_nginx" nginx_config
snapshot_path /etc/nginx/sites-enabled/eden-api nginx_enabled
snapshot_path /etc/nginx/sites-enabled/default nginx_default
snapshot_path /etc/nginx/conf.d/eden-limits.conf nginx_limits
snapshot_path "$previous_unit" api_unit
snapshot_path /etc/systemd/system/eden-phase1-soak.service phase1_soak_service
snapshot_path /etc/systemd/system/eden-phase1-soak.timer phase1_soak_timer
snapshot_path /etc/systemd/system/eden-phase2-soak.service phase2_soak_service
snapshot_path /etc/systemd/system/eden-phase2-soak.timer phase2_soak_timer
snapshot_path /etc/systemd/system/eden-phase1-query-plans.service phase1_query_plans_service
snapshot_path /etc/systemd/system/eden-phase1-query-plans.timer phase1_query_plans_timer
snapshot_path /etc/systemd/system/eden-db-maintenance-report.service db_maintenance_service
snapshot_path /etc/systemd/system/eden-db-maintenance-report.timer db_maintenance_timer
snapshot_path "$pilot_report_service" pilot_report_service
snapshot_path "$pilot_report_timer" pilot_report_timer
snapshot_path /etc/systemd/journald.conf.d/eden.conf journald_config
snapshot_path /etc/systemd/system/eden-api.service.d/20-phase1-baseline.conf baseline_dropin
snapshot_path /opt/eden/phase1-evidence/baseline.env baseline_env
snapshot_path /opt/eden/shared/.env runtime_env
snapshot_path /opt/eden/shared/migration.env migration_env
unit_state_dir="$(mktemp -d)"
capture_unit_state() {
  local unit="$1"
  if systemctl is-enabled "$unit" >/dev/null 2>&1; then
    printf 'enabled\n' >"$unit_state_dir/$unit.enabled"
  else
    printf 'disabled\n' >"$unit_state_dir/$unit.enabled"
  fi
  if systemctl is-active "$unit" >/dev/null 2>&1; then
    printf 'active\n' >"$unit_state_dir/$unit.active"
  else
    printf 'inactive\n' >"$unit_state_dir/$unit.active"
  fi
}
restore_unit_state() {
  local unit="$1"
  local enabled active
  enabled="$(<"$unit_state_dir/$unit.enabled")"
  active="$(<"$unit_state_dir/$unit.active")"
  if [[ "$active" == active ]]; then
    systemctl enable --now "$unit" 2>/dev/null || true
  else
    systemctl disable --now "$unit" 2>/dev/null || true
  fi
  if [[ "$enabled" == enabled ]]; then
    systemctl enable "$unit" 2>/dev/null || true
  else
    systemctl disable "$unit" 2>/dev/null || true
  fi
}
verify_unit_state() {
  local unit="$1"
  local expected_enabled expected_active
  expected_enabled="$(<"$unit_state_dir/$unit.enabled")"
  expected_active="$(<"$unit_state_dir/$unit.active")"
  if [[ "$expected_active" == active ]]; then
    systemctl is-active --quiet "$unit" || return 1
  else
    systemctl is-active --quiet "$unit" && return 1 || true
  fi
  if [[ "$expected_enabled" == enabled ]]; then
    systemctl is-enabled --quiet "$unit" || return 1
  else
    systemctl is-enabled --quiet "$unit" && return 1 || true
  fi
}
tracked_units=(
  nginx eden-api systemd-journald \
  eden-phase1-soak.service eden-phase1-soak.timer \
  eden-phase2-soak.service eden-phase2-soak.timer \
  eden-phase1-query-plans.service eden-phase1-query-plans.timer \
  eden-db-maintenance-report.service eden-db-maintenance-report.timer \
  eden-phase2-pilot-report.service eden-phase2-pilot-report.timer
)
for unit in "${tracked_units[@]}"; do
  capture_unit_state "$unit"
done
recover_previous_release() {
  if (( deployment_complete == 1 )); then
    rm -f -- "$release/runtime.env" "$release/migration.env"
    rm -rf -- "$rollback_dir" "$unit_state_dir"
    return
  fi
  local rollback_failed=0
  set +e
  for unit in "${tracked_units[@]}"; do
    case "$unit" in
      systemd-journald|nginx) ;;
      *) systemctl stop "$unit" 2>/dev/null || true ;;
    esac
  done
  if [[ -n "$previous_release" && -d "$previous_release" ]]; then
    ln -sfn "$previous_release" /opt/eden/current
  fi
  restore_snapshot /opt/eden/current current_release || rollback_failed=1
  if (( preserve_fail_closed == 0 )); then
    restore_snapshot "$previous_nginx" nginx_config || rollback_failed=1
    restore_snapshot /etc/nginx/sites-enabled/eden-api nginx_enabled || rollback_failed=1
    restore_snapshot /etc/nginx/sites-enabled/default nginx_default || rollback_failed=1
    restore_snapshot /etc/nginx/conf.d/eden-limits.conf nginx_limits || rollback_failed=1
  else
    # Keep the explicitly installed no-certificate fail-closed configuration.
    ln -sfn /etc/nginx/sites-available/eden-api /etc/nginx/sites-enabled/eden-api
  fi
  restore_snapshot "$previous_unit" api_unit || rollback_failed=1
  restore_snapshot /etc/systemd/system/eden-phase1-soak.service phase1_soak_service || rollback_failed=1
  restore_snapshot /etc/systemd/system/eden-phase1-soak.timer phase1_soak_timer || rollback_failed=1
  restore_snapshot /etc/systemd/system/eden-phase2-soak.service phase2_soak_service || rollback_failed=1
  restore_snapshot /etc/systemd/system/eden-phase2-soak.timer phase2_soak_timer || rollback_failed=1
  restore_snapshot /etc/systemd/system/eden-phase1-query-plans.service phase1_query_plans_service || rollback_failed=1
  restore_snapshot /etc/systemd/system/eden-phase1-query-plans.timer phase1_query_plans_timer || rollback_failed=1
  restore_snapshot /etc/systemd/system/eden-db-maintenance-report.service db_maintenance_service || rollback_failed=1
  restore_snapshot /etc/systemd/system/eden-db-maintenance-report.timer db_maintenance_timer || rollback_failed=1
  restore_snapshot "$pilot_report_service" pilot_report_service || rollback_failed=1
  restore_snapshot "$pilot_report_timer" pilot_report_timer || rollback_failed=1
  restore_snapshot /etc/systemd/journald.conf.d/eden.conf journald_config || rollback_failed=1
  restore_snapshot /etc/systemd/system/eden-api.service.d/20-phase1-baseline.conf baseline_dropin || rollback_failed=1
  restore_snapshot /opt/eden/phase1-evidence/baseline.env baseline_env || rollback_failed=1
  restore_snapshot /opt/eden/shared/.env runtime_env || rollback_failed=1
  restore_snapshot /opt/eden/shared/migration.env migration_env || rollback_failed=1
  rm -f -- "$release/runtime.env" "$release/migration.env" || rollback_failed=1
  nginx -t 2>/dev/null || rollback_failed=1
  systemctl daemon-reload 2>/dev/null || rollback_failed=1
  systemctl reset-failed eden-api 2>/dev/null || true
  for unit in "${tracked_units[@]}"; do
    restore_unit_state "$unit"
    verify_unit_state "$unit" || rollback_failed=1
  done
  if [[ "$(<"$unit_state_dir/systemd-journald.active")" == active ]]; then
    systemctl restart systemd-journald 2>/dev/null || rollback_failed=1
  fi
  systemctl reload nginx 2>/dev/null || rollback_failed=1
  for _ in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:8000/internal/readiness >/dev/null 2>&1; then
      rollback_readiness_ok=1
      break
    fi
    sleep 1
  done
  if [[ "${rollback_readiness_ok:-0}" != 1 ]]; then
    rollback_failed=1
    echo "Rollback readiness check failed after restoring the previous release." >&2
  fi
  if (( rollback_failed != 0 )); then
    echo "Rollback completed with verification failures; inspect systemd and Nginx state." >&2
  else
    echo "Rollback restored release, Nginx, units, timers, and config; readiness passed." >&2
  fi
  rm -rf -- "$rollback_dir" "$unit_state_dir"
  set -e
}
trap recover_previous_release EXIT

cd "$release"
# The native Python connector links against Connector/C. Ubuntu 26.04 provides 3.4+.
require_commands mariadb_config runuser
getent group eden >/dev/null 2>&1 || groupadd --system eden
install -d -o root -g eden -m 755 /opt/eden/python
export UV_PYTHON_INSTALL_DIR=/opt/eden/python
export UV_MANAGED_PYTHON=1
export UV_PYTHON=3.12
uv sync --frozen --no-dev
chmod -R a+rX /opt/eden/python
install -d -o root -g eden -m 750 /opt/eden/shared
for incoming in "$release/runtime.env" "$release/migration.env"; do
  [[ -f "$incoming" && ! -L "$incoming" ]] || {
    echo "Missing generated deployment environment." >&2
    exit 1
  }
done
install -o root -g eden -m 640 "$release/runtime.env" /opt/eden/shared/.env
install -o root -g root -m 600 "$release/migration.env" /opt/eden/shared/migration.env
rm -f -- "$release/runtime.env" "$release/migration.env"
[[ -f /opt/eden/shared/.env ]] || {
  echo "Missing /opt/eden/shared/.env." >&2
  exit 1
}
set -a
load_dotenv_file /opt/eden/shared/.env false
set +a
validate_migration_environment /opt/eden/shared/migration.env
set -a
load_dotenv_file /opt/eden/shared/migration.env false
set +a
unexport_sensitive_environment
is_true "${SNAPSHOT_RETENTION_ENABLED:-false}" || {
  echo "Runtime environment must set SNAPSHOT_RETENTION_ENABLED=true before Settings validation." >&2
  exit 1
}
: "${ENVIRONMENT:?Missing ENVIRONMENT}"
[[ "$ENVIRONMENT" == "production" ]] || {
  echo "ENVIRONMENT must be production on the public deployment." >&2
  exit 1
}
: "${DB_HOST:?Missing DB_HOST}"
: "${DB_PORT:?Missing DB_PORT}"
: "${DB_USER:?Missing DB_USER}"
: "${DB_PASSWORD:?Missing DB_PASSWORD}"
: "${INGESTION_DB_USER:?Missing INGESTION_DB_USER}"
: "${INGESTION_DB_PASSWORD:?Missing INGESTION_DB_PASSWORD}"
: "${MIGRATION_DB_USER:?Missing MIGRATION_DB_USER}"
: "${MIGRATION_DB_PASSWORD:?Missing MIGRATION_DB_PASSWORD}"
: "${DB_NAME:?Missing DB_NAME}"
: "${DB_NETWORK_MODE:?Missing DB_NETWORK_MODE}"
: "${DB_ALLOWED_CIDRS:?Missing DB_ALLOWED_CIDRS}"
: "${SCHEDULER_ENABLED:?Missing SCHEDULER_ENABLED}"
uv run python - <<'PY'
import sys

from app.config import get_settings
from app.repositories.database import verified_mariadb_connect_args

if sys.version_info[:2] != (3, 12):
    raise SystemExit("Production deployment requires Python 3.12.")
settings = get_settings()
if settings.ENVIRONMENT != "production":
    raise SystemExit("Application settings did not load in production mode.")
connect_args = verified_mariadb_connect_args(settings)
if connect_args["ssl"] is not True or connect_args["ssl_verify_cert"] is not True:
    raise SystemExit("Application database TLS verification is not fixed on.")
PY
[[ "$DB_USER" != "$INGESTION_DB_USER" && "$DB_USER" != "$MIGRATION_DB_USER" \
  && "$INGESTION_DB_USER" != "$MIGRATION_DB_USER" ]] || {
  echo "DB runtime, ingestion, and migration users must be distinct." >&2
  exit 1
}
python3 - "$DB_ALLOWED_CIDRS" <<'PY'
import ipaddress
import sys

raw_values = sys.argv[1].replace(",", " ").split()
if not raw_values:
    raise SystemExit("DB_ALLOWED_CIDRS must contain at least one network.")
for raw_value in raw_values:
    network = ipaddress.ip_network(raw_value, strict=False)
    if network.prefixlen == 0:
        raise SystemExit("DB_ALLOWED_CIDRS must not allow the entire internet.")
PY
case "$DB_NETWORK_MODE" in
  allowlist)
    if command -v ufw >/dev/null; then
      ufw_status="$(ufw status)" || {
        echo "DB network gate failed: ufw status could not be read." >&2
        exit 1
      }
      printf '%s\n' "$ufw_status" | grep -Eiq '^Status: active' || {
        echo "DB network gate failed: ufw is not active." >&2
        exit 1
      }
      printf '%s\n' "$ufw_status" \
        | uv run python scripts/verify_db_security.py firewall \
            --format ufw --allowed "$DB_ALLOWED_CIDRS"
    elif command -v nft >/dev/null; then
      nft_rules="$(nft list ruleset)" || {
        echo "DB network gate failed: nftables rules could not be read." >&2
        exit 1
      }
      printf '%s\n' "$nft_rules" \
        | uv run python scripts/verify_db_security.py firewall \
            --format nft --allowed "$DB_ALLOWED_CIDRS"
    else
      echo "DB network gate failed: no supported firewall evidence." >&2
      exit 1
    fi
    ;;
  ssh-tunnel)
    case "$(printf '%s' "$DB_HOST" | tr '[:upper:]' '[:lower:]')" in
      localhost|localhost.localdomain|127.0.0.1|::1|\[::1\]) ;;
      *) echo "DB network gate failed: SSH tunnel mode requires loopback DB_HOST." >&2; exit 1 ;;
    esac
    ;;
  *) echo "DB_NETWORK_MODE must be allowlist or ssh-tunnel." >&2; exit 1 ;;
esac

db_probe_config="$(mktemp)"
ingestion_probe_config="$(mktemp)"
cleanup_db_probe() { rm -f "$db_probe_config" "$ingestion_probe_config"; }
trap 'cleanup_db_probe; recover_previous_release' EXIT
chmod 600 "$db_probe_config"
{
  echo '[client]'
  echo "host=${DB_HOST}"
  echo "port=${DB_PORT}"
  echo "user=${DB_USER}"
  echo "password=${DB_PASSWORD}"
  echo 'ssl'
  echo 'ssl-verify-server-cert'
} >"$db_probe_config"
ssl_capability="$(mariadb --defaults-extra-file="$db_probe_config" \
  --ssl --ssl-verify-server-cert --batch --skip-column-names \
  -e "SHOW VARIABLES LIKE 'have_ssl'")"
ssl_cipher="$(mariadb --defaults-extra-file="$db_probe_config" \
  --ssl --ssl-verify-server-cert --batch --skip-column-names \
  -e "SHOW STATUS LIKE 'Ssl_cipher'")"
printf '%s\n' "$ssl_capability" | grep -Eiq $'have_ssl\t(YES|ON|1)' || {
  echo "DB TLS gate failed: MariaDB SSL capability is unavailable." >&2
  exit 1
}
printf '%s\n' "$ssl_cipher" | awk 'NR == 1 {print $2}' | grep -Eqv '^(|NULL)$' || {
  echo "DB TLS gate failed: connection is not encrypted." >&2
  exit 1
}
runtime_grants="$(mariadb --defaults-extra-file="$db_probe_config" \
  --ssl --ssl-verify-server-cert --batch --skip-column-names \
  -e "SHOW GRANTS FOR CURRENT_USER()")"
printf '%s\n' "$runtime_grants" \
  | uv run python scripts/verify_db_security.py grants \
      --role runtime --database "$DB_NAME"

chmod 600 "$ingestion_probe_config"
{
  echo '[client]'
  echo "host=${DB_HOST}"
  echo "port=${DB_PORT}"
  echo "user=${INGESTION_DB_USER}"
  echo "password=${INGESTION_DB_PASSWORD}"
  echo 'ssl'
  echo 'ssl-verify-server-cert'
} >"$ingestion_probe_config"
ingestion_grants="$(mariadb --defaults-extra-file="$ingestion_probe_config" \
  --ssl --ssl-verify-server-cert --batch --skip-column-names \
  -e "SHOW GRANTS FOR CURRENT_USER()" "$DB_NAME")"
printf '%s\n' "$ingestion_grants" \
  | uv run python scripts/verify_db_security.py grants \
      --role ingestion --database "$DB_NAME"
rm -f "$db_probe_config" "$ingestion_probe_config"
trap recover_previous_release EXIT
case "$(printf '%s' "$SCHEDULER_ENABLED" | tr '[:upper:]' '[:lower:]')" in
  true|1|yes|on) background_jobs_enabled=true ;;
  false|0|no|off) background_jobs_enabled=false ;;
  *) echo "Invalid SCHEDULER_ENABLED setting." >&2; exit 1 ;;
esac

install -d -m 750 /opt/eden/phase1-evidence /opt/eden/phase2-evidence
if getent group eden >/dev/null 2>&1; then
  :
else
  groupadd --system eden
fi
if id -u eden >/dev/null 2>&1; then
  :
else
  useradd --system --gid eden --home-dir /opt/eden --shell /usr/sbin/nologin eden
fi
chown -R root:eden "$release"
chmod -R u=rwX,g=rX,o= "$release"
chown -R eden:eden /opt/eden/phase1-evidence /opt/eden/phase2-evidence
install -d -m 750 /opt/eden/shared
chown root:eden /opt/eden/shared
chmod 750 /opt/eden/shared
if [[ -f /opt/eden/shared/.env ]]; then
  chown root:eden /opt/eden/shared/.env
  chmod 640 /opt/eden/shared/.env
fi
runuser -u eden -- "$release/.venv/bin/python" - <<'PY'
import importlib
import sys
import sysconfig

if sys.version_info[:2] != (3, 12):
    raise SystemExit("Production deployment requires Python 3.12.")
importlib.import_module(sysconfig._get_sysconfigdata_name())
PY

for unit in eden-phase1-soak eden-phase2-soak eden-phase2-pilot-report; do
  systemctl stop "$unit.timer" "$unit.service" 2>/dev/null || true
done
for unit in eden-phase1-query-plans eden-db-maintenance-report; do
  systemctl stop "$unit.timer" "$unit.service" 2>/dev/null || true
done
systemctl stop eden-api 2>/dev/null || true
runtime_user="$DB_USER"
runtime_password="$DB_PASSWORD"
export DB_USER="$MIGRATION_DB_USER"
export DB_PASSWORD="$MIGRATION_DB_PASSWORD"
alembic_heads="$(uv run alembic heads | awk 'NF {print $1}')"
[[ "$alembic_heads" == "20260911_0010" ]] || {
  echo "Migration preflight failed: expected one 0010 head." >&2
  exit 1
}
migration_current="$(uv run alembic current | awk 'NF {print $1}' | sort)"
case "$migration_current" in
  20260829_0007|$'20260829_0007\n20260911_0009'|20260911_0010)
    uv run alembic upgrade 20260911_0010
    ;;
  20260911_0009)
    echo "Database already includes alert retry tracking; contract remains gated."
    ;;
  ""|20260829_0008|20260829_0006|20260829_0005|20260829_0004|20260811_0003|20260811_0002|20260811_0001)
    uv run alembic upgrade 20260911_0009
    ;;
  *)
    echo "Migration preflight failed: unsupported current revision ${migration_current}." >&2
    exit 1
    ;;
esac
# Keep the legacy columns through the rollback and soak window. The separately
# gated contract finalizer applies 0007 only after the 24-hour evidence passes.

export DB_USER="$runtime_user"
export DB_PASSWORD="$runtime_password"
# Update registry policy only. Historical imports and product rebuilds must
# not bypass the scheduler capacity gate on every deployment.
if [[ "$background_jobs_enabled" == true ]]; then
  uv run python scripts/seed_reference.py
fi

ln -sfn "$release" /opt/eden/current
cat >/etc/systemd/system/eden-api.service <<'UNIT'
[Unit]
Description=EDEN API
After=network-online.target mariadb.service
Wants=network-online.target
Requires=mariadb.service
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
User=eden
Group=eden
WorkingDirectory=/opt/eden/current
EnvironmentFile=/opt/eden/shared/.env
UnsetEnvironment=MIGRATION_DB_USER MIGRATION_DB_PASSWORD VPS_PASSWORD
ExecStart=/opt/eden/current/.venv/bin/uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --workers 1 --limit-concurrency 32 --backlog 64
Restart=on-failure
RestartSec=5
CPUQuota=80%
MemoryHigh=512M
MemoryMax=768M
MemorySwapMax=128M
TasksMax=64
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict

[Install]
WantedBy=multi-user.target
UNIT

install -d -m 750 /opt/eden/phase1-evidence
cat >/etc/systemd/system/eden-phase1-soak.service <<'UNIT'
[Unit]
Description=EDEN Phase 1 soak evidence sample
After=eden-api.service mariadb.service
Requires=eden-api.service mariadb.service

[Service]
Type=oneshot
User=eden
Group=eden
WorkingDirectory=/opt/eden/current
EnvironmentFile=/opt/eden/shared/.env
UnsetEnvironment=MIGRATION_DB_USER MIGRATION_DB_PASSWORD VPS_PASSWORD
ExecStart=/opt/eden/current/.venv/bin/python scripts/phase1_soak.py sample
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/opt/eden/phase1-evidence
UNIT

cat >/etc/systemd/system/eden-phase2-soak.service <<'UNIT'
[Unit]
Description=EDEN independent Phase 2 soak evidence sample
After=eden-api.service mariadb.service
Requires=eden-api.service mariadb.service

[Service]
Type=oneshot
User=eden
Group=eden
WorkingDirectory=/opt/eden/current
EnvironmentFile=/opt/eden/shared/.env
UnsetEnvironment=MIGRATION_DB_USER MIGRATION_DB_PASSWORD VPS_PASSWORD
ExecStart=/opt/eden/current/.venv/bin/python scripts/phase2_soak.py sample --path /opt/eden/phase2-evidence/soak.jsonl
StandardOutput=journal
StandardError=journal
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/opt/eden/phase2-evidence
UNIT

cat >/etc/systemd/system/eden-phase2-soak.timer <<'UNIT'
[Unit]
Description=Collect EDEN independent Phase 2 soak evidence every five minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
AccuracySec=15s
Persistent=true
Unit=eden-phase2-soak.service

[Install]
WantedBy=timers.target
UNIT

cat >/etc/systemd/system/eden-phase2-pilot-report.service <<'UNIT'
[Unit]
Description=Generate the EDEN Phase 2 pilot report
After=mariadb.service
Requires=mariadb.service

[Service]
Type=oneshot
User=eden
Group=eden
WorkingDirectory=/opt/eden/current
EnvironmentFile=/opt/eden/shared/.env
UnsetEnvironment=MIGRATION_DB_USER MIGRATION_DB_PASSWORD VPS_PASSWORD
ExecStart=/opt/eden/current/.venv/bin/python scripts/phase2_pilot.py report --output /opt/eden/phase2-evidence/pilot-report.json
StandardOutput=journal
StandardError=journal
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/opt/eden/phase2-evidence

[Install]
WantedBy=multi-user.target
UNIT

cat >/etc/systemd/system/eden-phase2-pilot-report.timer <<'UNIT'
[Unit]
Description=Generate the EDEN Phase 2 pilot report daily

[Timer]
OnCalendar=*-*-* 04:15:00
Persistent=true
Unit=eden-phase2-pilot-report.service

[Install]
WantedBy=timers.target
UNIT

install -d -m 755 /etc/systemd/journald.conf.d
cat >/etc/systemd/journald.conf.d/eden.conf <<'JOURNAL'
[Journal]
SystemMaxUse=200M
RuntimeMaxUse=100M
MaxRetentionSec=14day
MaxFileSec=1day
JOURNAL

cat >/etc/systemd/system/eden-phase1-soak.timer <<'UNIT'
[Unit]
Description=Collect EDEN Phase 1 soak evidence every five minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
AccuracySec=15s
Persistent=true
Unit=eden-phase1-soak.service

[Install]
WantedBy=timers.target
UNIT

install -d -m 755 /var/www/eden-acme
cat >/etc/nginx/conf.d/eden-limits.conf <<'NGINX'
limit_conn_zone $binary_remote_addr zone=eden_per_ip:10m;
limit_req_zone $binary_remote_addr zone=eden_api_per_ip:1m rate=5r/s;
limit_req_zone $server_name zone=eden_api_total:1m rate=20r/s;
NGINX
write_nginx_fail_closed() {
  cat >/etc/nginx/sites-available/eden-api <<'NGINX'
server {
    listen 80;
    listen [::]:80;
    server_name api.edenapi.org;
    access_log /var/log/nginx/eden-api.access.log combined;
    error_log /var/log/nginx/eden-api.error.log warn;
    client_max_body_size 64k;
    client_body_timeout 10s;
    location ^~ /.well-known/acme-challenge/ { root /var/www/eden-acme; try_files $uri =404; }
    location / { return 503; }
}
NGINX
}

certificate="/etc/letsencrypt/live/api.edenapi.org/fullchain.pem"
certificate_key="/etc/letsencrypt/live/api.edenapi.org/privkey.pem"
if [[ ! -r "$certificate" || ! -r "$certificate_key" ]]; then
  write_nginx_fail_closed
  ln -sfn /etc/nginx/sites-available/eden-api /etc/nginx/sites-enabled/eden-api
  preserve_fail_closed=1
  if ! nginx -t; then
    systemctl stop nginx 2>/dev/null || true
    echo "Fail-closed Nginx configuration validation failed." >&2
    exit 1
  fi
  if ! systemctl reload nginx; then
    systemctl stop nginx 2>/dev/null || true
    echo "Fail-closed Nginx reload failed, so Nginx was stopped." >&2
    exit 1
  fi
  echo "TLS certificate is missing; deployment is fail-closed." >&2
  exit 1
fi
openssl x509 -in "$certificate" -noout -checkend 86400 >/dev/null
openssl x509 -in "$certificate" -noout -text >/dev/null
cat >/etc/nginx/sites-available/eden-api <<'NGINX'
server {
    listen 80;
    listen [::]:80;
    server_name api.edenapi.org;
    access_log /var/log/nginx/eden-api.access.log combined;
    error_log /var/log/nginx/eden-api.error.log warn;
    location ^~ /.well-known/acme-challenge/ { root /var/www/eden-acme; try_files $uri =404; }
    location / { return 308 https://$host$request_uri; }
}
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name api.edenapi.org;
    access_log /var/log/nginx/eden-api.access.log combined;
    error_log /var/log/nginx/eden-api.error.log warn;
    client_max_body_size 64k;
    client_body_timeout 10s;
    limit_conn eden_per_ip 20;
    ssl_certificate /etc/letsencrypt/live/api.edenapi.org/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.edenapi.org/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Content-Type-Options nosniff always;
    location /internal/ {
        allow 127.0.0.1;
        allow ::1;
        deny all;
        proxy_pass http://127.0.0.1:8000;
    }
    location = /dashboard { return 410; }
    location ^~ /dashboard/ { return 410; }
    location = /assets { return 410; }
    location ^~ /assets/ { return 410; }
    location ~ ^/(?:docs|openapi[.]json)?$ {
        limit_req zone=eden_api_per_ip burst=20 nodelay;
        limit_req zone=eden_api_total burst=40 nodelay;
        limit_req_status 429;
        access_log /var/log/nginx/eden-api-client.access.log combined;
        error_log /var/log/nginx/eden-api-client.error.log warn;
        proxy_connect_timeout 5s;
        proxy_read_timeout 30s;
        proxy_send_timeout 30s;
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    location ^~ /v1/ {
        limit_req zone=eden_api_per_ip burst=20 nodelay;
        limit_req zone=eden_api_total burst=40 nodelay;
        limit_req_status 429;
        access_log /var/log/nginx/eden-api-client.access.log combined;
        error_log /var/log/nginx/eden-api-client.error.log warn;
        proxy_connect_timeout 5s;
        proxy_read_timeout 30s;
        proxy_send_timeout 30s;
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    location / { return 404; }
}
NGINX
ln -sfn /etc/nginx/sites-available/eden-api /etc/nginx/sites-enabled/eden-api
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl daemon-reload

# Capture the scheduler-off performance fixture against the new release before
# starting the 24-hour concurrent scheduler/API soak.
install -d -m 755 /etc/systemd/system/eden-api.service.d
printf 'SCHEDULER_ENABLED=false\n' >/opt/eden/phase1-evidence/baseline.env
chown eden:eden /opt/eden/phase1-evidence/baseline.env
chmod 600 /opt/eden/phase1-evidence/baseline.env
cat >/etc/systemd/system/eden-api.service.d/20-phase1-baseline.conf <<'UNIT'
[Service]
EnvironmentFile=/opt/eden/phase1-evidence/baseline.env
UNIT
systemctl daemon-reload
systemctl reset-failed eden-api
systemctl enable --now nginx eden-api
systemctl reload nginx
for _ in $(seq 1 30); do
  curl -fsS http://127.0.0.1:8000/internal/readiness >/dev/null && break
  sleep 1
done
curl -fsS http://127.0.0.1:8000/internal/readiness >/dev/null
if [[ "$background_jobs_enabled" == true ]]; then
  uv run python scripts/phase1_soak.py baseline --iterations 20
fi

rm -f /etc/systemd/system/eden-api.service.d/20-phase1-baseline.conf
rm -f /opt/eden/phase1-evidence/baseline.env
systemctl daemon-reload
systemctl restart eden-api
systemctl restart systemd-journald

for _ in $(seq 1 30); do
  curl -fsS http://127.0.0.1:8000/internal/readiness >/dev/null && break
  sleep 1
done
curl -fsS http://127.0.0.1:8000/internal/readiness >/dev/null
# The API process owns the bounded payload cache. Prime it after the
# scheduler-on restart so the first timed sample measures steady-state work.
uv run python scripts/phase1_soak.py warmup >/dev/null
if [[ "$background_jobs_enabled" == true ]]; then
  systemctl start eden-phase2-pilot-report.service
fi
curl -fsS --max-time 10 https://api.edenapi.org/openapi.json >/dev/null
curl -fsS --max-time 10 https://api.edenapi.org/docs >/dev/null
[[ "$(curl -sS --max-time 10 -o /dev/null -w '%{http_code}' https://api.edenapi.org/dashboard/)" == "410" ]]
curl -fsS --max-time 10 'https://api.edenapi.org/v1/trends?keyword=%EC%A0%9C%EC%A3%BC&period=7d&time_unit=day&limit=1' >/dev/null
for unit in eden-phase1-soak eden-phase2-soak eden-phase2-pilot-report; do
  if [[ "$background_jobs_enabled" == true ]]; then
    systemctl enable --now "$unit.timer"
  else
    systemctl disable --now "$unit.timer"
    systemctl stop "$unit.service"
  fi
done
# Retire report-only units from older releases. Their prior state is included
# in rollback above until the deployment finishes.
for unit in eden-phase1-query-plans eden-db-maintenance-report; do
  if [[ -f "/etc/systemd/system/$unit.timer" ]]; then
    systemctl disable --now "$unit.timer"
  fi
  rm -f "/etc/systemd/system/$unit.timer" "/etc/systemd/system/$unit.service"
done
systemctl daemon-reload
deployment_complete=1

cleanup_legacy_dashboard_artifacts() {
  local deployed_release legacy_dashboard cleanup_failed=0
  rm -rf -- /var/www/eden-dashboard || cleanup_failed=1
  for deployed_release in /opt/eden/releases/*; do
    [[ -d "$deployed_release" && ! -L "$deployed_release" ]] || continue
    legacy_dashboard="$deployed_release/dashboard"
    if [[ -e "$legacy_dashboard" || -L "$legacy_dashboard" ]]; then
      rm -rf -- "$legacy_dashboard" || cleanup_failed=1
    fi
  done
  rm -f -- \
    /var/log/nginx/eden-dashboard.access.log \
    /var/log/nginx/eden-dashboard.error.log || cleanup_failed=1
  return "$cleanup_failed"
}
if ! cleanup_legacy_dashboard_artifacts; then
  echo "API deployment succeeded, but some legacy dashboard artifacts could not be removed." >&2
fi

# These legacy timers call scripts that no longer ship. The current phase1 and
# phase2 timers above own soak evidence; do not leave failed duplicate jobs alive.
for unit in eden-maintenance eden-soak-sample; do
  systemctl disable --now "$unit.timer" 2>/dev/null || true
  systemctl stop "$unit.service" 2>/dev/null || true
  systemctl reset-failed "$unit.service" 2>/dev/null || true
  rm -f "/etc/systemd/system/$unit.timer" "/etc/systemd/system/$unit.service"
done
systemctl daemon-reload
