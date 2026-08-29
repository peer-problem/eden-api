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

load_runtime_environment() {
  if [[ -f /opt/eden/shared/.env ]]; then
    set -a
    source /opt/eden/shared/.env
    set +a
    if [[ -e /opt/eden/shared/migration.env || -L /opt/eden/shared/migration.env ]]; then
      validate_migration_environment /opt/eden/shared/migration.env
      set -a
      source /opt/eden/shared/migration.env
      set +a
    fi
  elif [[ -f .env ]]; then
    set -a
    source .env
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
