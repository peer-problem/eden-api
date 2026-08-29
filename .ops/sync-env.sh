#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

AUTO_YES=false
[[ "${1:-}" == "--yes" ]] && AUTO_YES=true

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
: "${VPS_IP_ADDRESS:?Missing VPS_IP_ADDRESS}"
VPS_USERNAME="${VPS_USERNAME:-${VPS_USRENAME:-}}"
: "${VPS_USERNAME:?Missing VPS_USERNAME}"
: "${VPS_PASSWORD:?Missing VPS_PASSWORD}"
VPS_HOST_FINGERPRINT="${VPS_HOST_FINGERPRINT:-${VPS_SSH_HOST_FINGERPRINT:-}}"
: "${VPS_HOST_FINGERPRINT:?Missing VPS_HOST_FINGERPRINT}"

allowed=(
  ENVIRONMENT LOG_LEVEL EDEN_TIMEZONE
  MAX_REQUEST_BODY_BYTES MAX_RESPONSE_BODY_BYTES
  DB_HOST DB_PORT DB_NAME DB_USER DB_PASSWORD DB_CONNECT_TIMEOUT_SECONDS
  DB_POOL_SIZE DB_MAX_OVERFLOW SCHEDULER_DB_POOL_SIZE DB_POOL_RECYCLE_SECONDS
  DB_SSL_CA DB_SSL_VERIFY_CERT DB_NETWORK_MODE DB_ALLOWED_CIDRS
  INGESTION_DB_USER INGESTION_DB_PASSWORD
  SCHEDULER_ENABLED PUBLIC_BASE_URL
  SOURCE_HTTP_TIMEOUT_SECONDS SOURCE_MAX_RESPONSE_BYTES SOURCE_WORKERS PRODUCT_WORKERS
  RAW_PERSIST_BATCH_SIZE DEAD_LETTER_BATCH_SIZE SNAPSHOT_PROVENANCE_BATCH_SIZE
  SNAPSHOT_RETENTION_DAYS SNAPSHOT_RETENTION_BATCH_SIZE SNAPSHOT_RETENTION_ENABLED
  DERIVED_DAILY_GROWTH_BUDGET_BYTES DISK_WARNING_PERCENT DISK_PRODUCT_PAUSE_PERCENT
  DISK_SOURCE_PAUSE_PERCENT
  PUBLIC_DATA_SERVICE_KEY NAVER_CLIENT_ID NAVER_CLIENT_SECRET YOUTUBE_API_KEY
  X_BEARER_TOKEN KMA_SERVICE_KEY KEXIM_API_KEY
  BOK_ECOS_API_KEY LLM_API_KEY LLM_MODEL
)
allowed_csv="$(IFS=,; echo "${allowed[*]}")"
temporary_dir="$(mktemp -d)"
trap 'rm -rf "$temporary_dir"' EXIT
patch_file="$temporary_dir/eden.env.patch"

: >"$patch_file"
for key in "${allowed[@]}"; do
  line="$(grep -E "^${key}=" .env | tail -n 1 || true)"
  [[ -z "$line" ]] || printf '%s\n' "$line" >>"$patch_file"
done
chmod 600 "$patch_file"
unexport_sensitive_environment

echo "Destination: ${VPS_USERNAME}@${VPS_IP_ADDRESS}"
cut -d= -f1 "$patch_file" | sed 's/^/  - /'
if [[ "$AUTO_YES" != true ]]; then
  read -r -p "Apply these environment updates? [y/N] " answer
  [[ "$answer" == "y" || "$answer" == "Y" ]] || exit 1
fi

export SSHPASS="$VPS_PASSWORD"
require_commands() {
  local command_name
  for command_name in "$@"; do
    command -v "$command_name" >/dev/null || {
      echo "Missing required command: ${command_name}" >&2
      return 1
    }
  done
}
require_commands ssh-keyscan ssh-keygen sshpass scp ssh
known_hosts_file="$(mktemp)"
known_hosts_raw="$(mktemp)"
cleanup_ssh() { rm -f "$known_hosts_file" "$known_hosts_raw"; }
cleanup_all() { cleanup_ssh; rm -rf "$temporary_dir"; }
trap cleanup_all EXIT
ssh-keyscan -T 10 -t ed25519,rsa,ecdsa "$VPS_IP_ADDRESS" >"$known_hosts_raw" 2>/dev/null || {
  echo "VPS host-key lookup failed." >&2
  exit 1
}
expected_fingerprints="$(
  printf '%s\n' "$VPS_HOST_FINGERPRINT" \
    | tr ',;' '\n\n' \
    | awk '{for (field_index = 1; field_index <= NF; field_index++) print $field_index}' \
    | sort -u
)"
[[ -n "$expected_fingerprints" ]] || { echo "VPS host fingerprint pin is empty." >&2; exit 1; }
while IFS= read -r fingerprint; do
  [[ "$fingerprint" =~ ^SHA256:[A-Za-z0-9+/]+={0,2}$ ]] || {
    echo "VPS host fingerprint pin is malformed." >&2
    exit 1
  }
done <<<"$expected_fingerprints"
actual_fingerprints="$(
  ssh-keygen -lf "$known_hosts_raw" -E sha256 \
    | awk '$2 ~ /^SHA256:/ {print $2}' \
    | sort -u
)"
[[ -n "$actual_fingerprints" && "$actual_fingerprints" == "$expected_fingerprints" ]] || {
  echo "VPS host fingerprint verification failed: advertised keys do not exactly match the pin." >&2
  exit 1
}
cp "$known_hosts_raw" "$known_hosts_file"
chmod 600 "$known_hosts_file" "$known_hosts_raw"
ssh_options=(-o "UserKnownHostsFile=$known_hosts_file" -o StrictHostKeyChecking=yes)
remote_patch="/tmp/eden-env-patch-${RANDOM}-$$"
sshpass -e scp "${ssh_options[@]}" -q "$patch_file" \
  "${VPS_USERNAME}@${VPS_IP_ADDRESS}:${remote_patch}"
sshpass -e ssh "${ssh_options[@]}" \
  "${VPS_USERNAME}@${VPS_IP_ADDRESS}" \
  "chmod 600 '${remote_patch}'"
sshpass -e ssh "${ssh_options[@]}" \
  "${VPS_USERNAME}@${VPS_IP_ADDRESS}" \
  "getent group eden >/dev/null 2>&1 || groupadd --system eden; install -d -o root -g eden -m 750 /opt/eden/shared"
sshpass -e ssh "${ssh_options[@]}" \
  "${VPS_USERNAME}@${VPS_IP_ADDRESS}" \
  "umask 077; trap 'rm -f ${remote_patch}' EXIT; python3 - /opt/eden/shared/.env ${remote_patch} ${allowed_csv}" <<'PY'
import os
import sys
import tempfile
from pathlib import Path

target = Path(sys.argv[1])
patch = Path(sys.argv[2])
allowed = set(sys.argv[3].split(","))


def values(path: Path) -> dict[str, str]:
    result = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            result[stripped.split("=", 1)[0]] = line
    return result


incoming = values(patch)
updates = {key: value for key, value in incoming.items() if key in allowed}
forbidden = {
    "MIGRATION_DB_PASSWORD",
    "MIGRATION_DB_USER",
    "VPS_PASSWORD",
}
lines = target.read_text().splitlines() if target.is_file() else []
output = []
written = set()
for line in lines:
    key = line.strip().split("=", 1)[0] if "=" in line else ""
    if key in forbidden:
        continue
    if key in updates:
        output.append(updates[key])
        written.add(key)
    else:
        output.append(line)
output.extend(updates[key] for key in sorted(set(updates) - written))

descriptor, temporary = tempfile.mkstemp(prefix=".env.", dir=target.parent)
with os.fdopen(descriptor, "w") as handle:
    handle.write("\n".join(output).rstrip() + "\n")
os.chmod(temporary, 0o600)
os.replace(temporary, target)
PY
sshpass -e ssh "${ssh_options[@]}" \
  "${VPS_USERNAME}@${VPS_IP_ADDRESS}" \
  "install -d -m 750 /opt/eden/shared && chown root:eden /opt/eden/shared && chmod 750 /opt/eden/shared && chmod 640 /opt/eden/shared/.env && chown root:eden /opt/eden/shared/.env && rm -f '${remote_patch}'"

echo "Production environment updated. Restart with: .ops/deploy.sh"
