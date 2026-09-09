#!/usr/bin/env bash

# Shared deployment functions.
require_commands() {
  local command_name
  for command_name in "$@"; do
    command -v "$command_name" >/dev/null || {
      echo "Missing required command: ${command_name}" >&2
      return 1
    }
  done
}

require_environment() {
  local key
  for key in "$@"; do
    [[ -n "${!key:-}" ]] || {
      echo "Missing required environment key: ${key}" >&2
      return 1
    }
  done
}

is_true() {
  case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
    true|1|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

unexport_sensitive_environment() {
  local key
  for key in \
    MIGRATION_DB_USER MIGRATION_DB_PASSWORD VPS_PASSWORD VPS_HOST_FINGERPRINT \
    VPS_SSH_HOST_FINGERPRINT SSHPASS; do
    export -n "$key" 2>/dev/null || true
  done
}

validate_migration_environment() {
  local migration_path="${1:?Missing migration environment path}"
  local descriptor
  [[ -f "$migration_path" && ! -L "$migration_path" ]] || {
    echo "Missing root-protected ${migration_path}." >&2
    return 1
  }
  descriptor="$(stat -c '%U:%G:%a' "$migration_path" 2>/dev/null || true)"
  if [[ -z "$descriptor" ]]; then
    descriptor="$(stat -f '%Su:%Sg:%Lp' "$migration_path" 2>/dev/null || true)"
  fi
  [[ "$descriptor" == "root:root:600" ]] || {
    echo "${migration_path} must be owned by root:root with mode 600." >&2
    return 1
  }
}

verify_ssh_host_key() {
  local address="$1"
  local expected_pin="$2"
  local raw_hosts="$3"
  local pinned_hosts="$4"
  local expected_fingerprints actual_fingerprints

  ssh-keyscan -T 10 -t ed25519,rsa,ecdsa "$address" >"$raw_hosts" 2>/dev/null || {
    echo "VPS host-key lookup failed." >&2
    return 1
  }
  expected_fingerprints="$(
    printf '%s\n' "$expected_pin" \
      | tr ',;' '\n\n' \
      | awk '{for (field_index = 1; field_index <= NF; field_index++) print $field_index}' \
      | sort -u
  )"
  [[ -n "$expected_fingerprints" ]] || {
    echo "VPS host fingerprint pin is empty." >&2
    return 1
  }
  while IFS= read -r fingerprint; do
    [[ "$fingerprint" =~ ^SHA256:[A-Za-z0-9+/]+={0,2}$ ]] || {
      echo "VPS host fingerprint pin is malformed." >&2
      return 1
    }
  done <<<"$expected_fingerprints"
  actual_fingerprints="$(
    ssh-keygen -lf "$raw_hosts" -E sha256 \
      | awk '$2 ~ /^SHA256:/ {print $2}' \
      | sort -u
  )"
  [[ -n "$actual_fingerprints" && "$actual_fingerprints" == "$expected_fingerprints" ]] || {
    echo "VPS host fingerprint verification failed: advertised keys do not exactly match the pin." >&2
    return 1
  }
  cp "$raw_hosts" "$pinned_hosts"
  chmod 600 "$pinned_hosts" "$raw_hosts"
}

# Parse dotenv as data. NUL delimiters preserve spaces, quotes and multiline values.
load_dotenv_file() {
  local env_path="$1" interpolate="${2:-true}" values_file key value
  values_file="$(mktemp)"
  chmod 600 "$values_file"
  if ! uv run --frozen --no-sync python - "$env_path" "$interpolate" >"$values_file" <<'PYENV'
import re
import sys
from pathlib import Path
from dotenv import dotenv_values
if not Path(sys.argv[1]).is_file():
    raise SystemExit("Missing dotenv file")
for key, value in dotenv_values(sys.argv[1], interpolate=sys.argv[2] == "true").items():
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        raise SystemExit("Invalid dotenv key")
    if value is not None:
        if "\0" in value:
            raise SystemExit("NUL bytes are not supported in environment values")
        sys.stdout.buffer.write(key.encode() + b"\0" + value.encode() + b"\0")
PYENV
  then
    rm -f "$values_file"
    return 1
  fi
  while IFS= read -r -d '' key && IFS= read -r -d '' value; do
    printf -v "$key" '%s' "$value"
    export "$key"
  done <"$values_file"
  rm -f "$values_file"
}

load_runtime_environment() {
  if [[ -f /opt/eden/shared/.env ]]; then
    set -a
    load_dotenv_file /opt/eden/shared/.env false
    set +a
    if [[ -e /opt/eden/shared/migration.env || -L /opt/eden/shared/migration.env ]]; then
      validate_migration_environment /opt/eden/shared/migration.env
      set -a
      load_dotenv_file /opt/eden/shared/migration.env false
      set +a
    fi
  elif [[ -f .env ]]; then
    set -a
    load_dotenv_file .env
    set +a
  fi
  unexport_sensitive_environment
}

phase2_soak_status() {
  load_runtime_environment
  require_commands uv
  local evidence_path="${1:-${PHASE2_SOAK_EVIDENCE_PATH:-/opt/eden/phase2-evidence/soak.jsonl}}"
  [[ "${SNAPSHOT_RETENTION_ENABLED:-false}" == true ]] || {
    echo "Phase 2 soak requires SNAPSHOT_RETENTION_ENABLED=true in the runtime environment." >&2
    return 1
  }
  # scripts/phase2_soak.py owns the independent seven-day production gate.
  uv run python scripts/phase2_soak.py status --path "$evidence_path"
}
