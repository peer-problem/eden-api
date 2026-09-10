from __future__ import annotations

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REMOTE_DEPLOY = (REPOSITORY_ROOT / "scripts" / "deploy_remote.sh").read_text(encoding="utf-8")


def test_nginx_exposes_only_api_and_documentation_routes() -> None:
    assert "location ~ ^/(?:docs|openapi[.]json)?$" in REMOTE_DEPLOY
    assert "location ^~ /v1/" in REMOTE_DEPLOY
    assert "location /internal/" in REMOTE_DEPLOY
    assert "location = /dashboard { return 410; }" in REMOTE_DEPLOY
    assert "location ^~ /dashboard/ { return 410; }" in REMOTE_DEPLOY
    assert "location = /assets { return 410; }" in REMOTE_DEPLOY
    assert "location ^~ /assets/ { return 410; }" in REMOTE_DEPLOY
    assert "location / { return 404; }" in REMOTE_DEPLOY
    assert "alias /var/www/eden-dashboard" not in REMOTE_DEPLOY
    assert "try_files $uri $uri/ /dashboard/index.html" not in REMOTE_DEPLOY


def test_documentation_and_api_keep_the_public_rate_limits() -> None:
    assert "zone=eden_api_per_ip:1m rate=5r/s" in REMOTE_DEPLOY
    assert "zone=eden_api_total:1m rate=20r/s" in REMOTE_DEPLOY
    assert REMOTE_DEPLOY.count("zone=eden_api_per_ip burst=20 nodelay") == 2
    assert REMOTE_DEPLOY.count("zone=eden_api_total burst=40 nodelay") == 2
    assert "limit_conn eden_per_ip 20" in REMOTE_DEPLOY


def test_api_resource_limits_and_nginx_reload_are_preserved() -> None:
    assert "CPUQuota=80%" in REMOTE_DEPLOY
    assert "MemoryMax=768M" in REMOTE_DEPLOY
    assert "systemctl reload nginx" in REMOTE_DEPLOY
    assert "curl -fsS --max-time 10 https://api.edenapi.org/openapi.json" in REMOTE_DEPLOY


def test_successful_deploy_removes_only_known_dashboard_artifacts() -> None:
    cleanup_start = REMOTE_DEPLOY.index("cleanup_legacy_dashboard_artifacts()")
    cleanup_call = REMOTE_DEPLOY.index("if ! cleanup_legacy_dashboard_artifacts")
    completed = REMOTE_DEPLOY.index("deployment_complete=1")

    assert completed < cleanup_start < cleanup_call
    assert "rm -rf -- /var/www/eden-dashboard" in REMOTE_DEPLOY
    assert "for deployed_release in /opt/eden/releases/*" in REMOTE_DEPLOY
    assert 'legacy_dashboard="$deployed_release/dashboard"' in REMOTE_DEPLOY
    assert 'rm -rf -- "$legacy_dashboard"' in REMOTE_DEPLOY
    assert "/var/log/nginx/eden-dashboard.access.log" in REMOTE_DEPLOY
    assert "/var/log/nginx/eden-dashboard.error.log" in REMOTE_DEPLOY
