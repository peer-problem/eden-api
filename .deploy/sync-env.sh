#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

AUTO_YES=false
[[ "${1:-}" == "--yes" ]] && AUTO_YES=true

set -a
source .env
set +a
: "${VPS_IP_ADDRESS:?Missing VPS_IP_ADDRESS}"
: "${VPS_USRENAME:?Missing VPS_USRENAME}"
: "${VPS_PASSWORD:?Missing VPS_PASSWORD}"

allowed=(
  LOG_LEVEL EDEN_TIMEZONE SOURCE_HTTP_TIMEOUT_SECONDS SOURCE_MAX_RESPONSE_BYTES
  PUBLIC_DATA_SERVICE_KEY NAVER_CLIENT_ID NAVER_CLIENT_SECRET YOUTUBE_API_KEY
  META_ACCESS_TOKEN TIKTOK_CLIENT_KEY TIKTOK_CLIENT_SECRET X_BEARER_TOKEN
  REDDIT_CLIENT_ID REDDIT_CLIENT_SECRET KMA_SERVICE_KEY KEXIM_API_KEY
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

echo "Destination: ${VPS_USRENAME}@${VPS_IP_ADDRESS}"
cut -d= -f1 "$patch_file" | sed 's/^/  - /'
if [[ "$AUTO_YES" != true ]]; then
  read -r -p "Apply these environment updates? [y/N] " answer
  [[ "$answer" == "y" || "$answer" == "Y" ]] || exit 1
fi

export SSHPASS="$VPS_PASSWORD"
remote_patch="/tmp/eden-env-patch-$$"
sshpass -e scp -q -o StrictHostKeyChecking=accept-new "$patch_file" \
  "${VPS_USRENAME}@${VPS_IP_ADDRESS}:${remote_patch}"
sshpass -e ssh -o StrictHostKeyChecking=accept-new \
  "${VPS_USRENAME}@${VPS_IP_ADDRESS}" \
  "python3 - /opt/eden/shared/.env ${remote_patch} ${allowed_csv}" <<'PY'
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
lines = target.read_text().splitlines()
output = []
written = set()
for line in lines:
    key = line.strip().split("=", 1)[0] if "=" in line else ""
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
sshpass -e ssh -o StrictHostKeyChecking=accept-new \
  "${VPS_USRENAME}@${VPS_IP_ADDRESS}" \
  "chmod 600 /opt/eden/shared/.env && rm -f ${remote_patch}"

echo "Production environment updated. Restart with: .deploy/deploy.sh"
