#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

[[ -f .env ]] || { echo "Missing .env" >&2; exit 1; }
set -a
source .env
set +a
: "${VPS_IP_ADDRESS:?Missing VPS_IP_ADDRESS}"
: "${VPS_USRENAME:?Missing VPS_USRENAME}"
: "${VPS_PASSWORD:?Missing VPS_PASSWORD}"

uv lock --check
uv run ruff check app scripts migrations

release_id="$(date -u +%Y%m%dT%H%M%SZ)"
remote_release="/opt/eden/releases/${release_id}"
export SSHPASS="$VPS_PASSWORD"
ssh_options=(-o StrictHostKeyChecking=accept-new)

sshpass -e ssh "${ssh_options[@]}" "${VPS_USRENAME}@${VPS_IP_ADDRESS}" \
  "install -d -m 750 '${remote_release}'"
export RSYNC_RSH="sshpass -e ssh -o StrictHostKeyChecking=accept-new"
rsync -az --delete \
  --exclude '.git/' --exclude '.venv/' --exclude '.env' --exclude '.agent/' \
  --exclude 'tmp/' --exclude '__pycache__/' --exclude '.ruff_cache/' \
  ./ "${VPS_USRENAME}@${VPS_IP_ADDRESS}:${remote_release}/"

sshpass -e ssh "${ssh_options[@]}" "${VPS_USRENAME}@${VPS_IP_ADDRESS}" \
  bash -s -- "$remote_release" <<'REMOTE'
set -euo pipefail
release="$1"
cd "$release"

uv sync --frozen --no-dev
set -a
source /opt/eden/shared/.env
set +a

systemctl stop eden-api 2>/dev/null || true
runtime_user="$DB_USER"
runtime_password="$DB_PASSWORD"
export DB_USER="$MIGRATION_DB_USER"
export DB_PASSWORD="$MIGRATION_DB_PASSWORD"
uv run alembic upgrade head

export DB_USER="$runtime_user"
export DB_PASSWORD="$runtime_password"
uv run python scripts/seed_reference.py
uv run python scripts/import_mois_areas.py
uv run python scripts/seed_reference.py
uv run python scripts/import_kto_inbound.py
uv run python scripts/import_keta_notices.py

ln -sfn "$release" /opt/eden/current
cat >/etc/systemd/system/eden-api.service <<'UNIT'
[Unit]
Description=EDEN API
After=network-online.target mariadb.service
Wants=network-online.target
Requires=mariadb.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/eden/current
EnvironmentFile=/opt/eden/shared/.env
ExecStart=/opt/eden/current/.venv/bin/uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ReadWritePaths=/opt/eden

[Install]
WantedBy=multi-user.target
UNIT

install -d -m 755 /var/www/eden-acme
if [[ -f /etc/letsencrypt/live/api.edenapi.org/fullchain.pem ]]; then
  cat >/etc/nginx/sites-available/eden-api <<'NGINX'
server {
    listen 80;
    listen [::]:80;
    server_name api.edenapi.org;
    location ^~ /.well-known/acme-challenge/ { root /var/www/eden-acme; try_files $uri =404; }
    location / { return 308 https://$host$request_uri; }
}
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name api.edenapi.org;
    ssl_certificate /etc/letsencrypt/live/api.edenapi.org/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.edenapi.org/privkey.pem;
    location /internal/ {
        allow 127.0.0.1;
        allow ::1;
        deny all;
        proxy_pass http://127.0.0.1:8000;
    }
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
NGINX
else
  cat >/etc/nginx/sites-available/eden-api <<'NGINX'
server {
    listen 80;
    listen [::]:80;
    server_name api.edenapi.org;
    location ^~ /.well-known/acme-challenge/ { root /var/www/eden-acme; try_files $uri =404; }
    location /internal/ {
        allow 127.0.0.1;
        allow ::1;
        deny all;
        proxy_pass http://127.0.0.1:8000;
    }
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
NGINX
fi
ln -sfn /etc/nginx/sites-available/eden-api /etc/nginx/sites-enabled/eden-api
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl daemon-reload
systemctl enable --now nginx eden-api
systemctl restart eden-api

for _ in $(seq 1 30); do
  curl -fsS http://127.0.0.1:8000/internal/readiness >/dev/null && break
  sleep 1
done
curl -fsS http://127.0.0.1:8000/internal/readiness >/dev/null
REMOTE

echo "Deployed ${release_id}."
