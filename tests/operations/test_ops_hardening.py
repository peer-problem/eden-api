from __future__ import annotations

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEPLOY = "\n".join(
    path.read_text(encoding="utf-8")
    for path in (
        REPOSITORY_ROOT / ".ops" / "deploy.sh",
        REPOSITORY_ROOT / "scripts" / "deploy_support.sh",
        REPOSITORY_ROOT / "scripts" / "deploy_remote.sh",
    )
)
RUN = (REPOSITORY_ROOT / ".ops" / "run.sh").read_text(encoding="utf-8")
SYNC = (REPOSITORY_ROOT / ".ops" / "sync-env.sh").read_text(encoding="utf-8")


def test_ssh_pinning_requires_the_exact_advertised_key_set() -> None:
    for source in (DEPLOY, SYNC):
        assert 'sort -u' in source
        assert '"$actual_fingerprints" == "$expected_fingerprints"' in source
        assert "advertised keys do not exactly match the pin" in source
        assert "StrictHostKeyChecking=yes" in source


def test_firewall_rules_correlate_port_action_and_source() -> None:
    assert DEPLOY.count("scripts/verify_db_security.py firewall") == 2
    assert '--format ufw --allowed "$DB_ALLOWED_CIDRS"' in DEPLOY
    assert '--format nft --allowed "$DB_ALLOWED_CIDRS"' in DEPLOY


def test_ingestion_grants_require_insert_on_the_configured_database() -> None:
    assert '"SHOW GRANTS FOR CURRENT_USER()" "$DB_NAME"' in DEPLOY
    assert DEPLOY.count("scripts/verify_db_security.py grants") == 2
    assert '--role runtime --database "$DB_NAME"' in DEPLOY
    assert '--role ingestion --database "$DB_NAME"' in DEPLOY


def test_every_migration_environment_loader_enforces_root_mode_six_hundred() -> None:
    assert DEPLOY.count("validate_migration_environment /opt/eden/shared/migration.env") == 2
    assert "! -L \"$migration_path\"" in DEPLOY
    assert DEPLOY.count("root:root:600") == 1
    assert 'source "$release/scripts/deploy_support.sh"' in DEPLOY


def test_rollback_covers_phase2_units_and_verifies_readiness() -> None:
    for unit in (
        "eden-phase1-soak.service",
        "eden-phase1-soak.timer",
        "eden-phase2-soak.service",
        "eden-phase2-soak.timer",
        "eden-phase1-query-plans.service",
        "eden-phase1-query-plans.timer",
        "eden-db-maintenance-report.service",
        "eden-db-maintenance-report.timer",
        "eden-phase2-pilot-report.service",
        "eden-phase2-pilot-report.timer",
    ):
        assert unit in DEPLOY
    assert "snapshot_path /etc/nginx/conf.d/eden-limits.conf" in DEPLOY
    assert "verify_unit_state" in DEPLOY
    assert "rollback_readiness_ok" in DEPLOY
    assert "Rollback restored release, dashboard, Nginx, units, timers, and config" in DEPLOY


def test_runtime_helpers_do_not_export_migration_or_vps_secrets() -> None:
    for source in (DEPLOY, RUN, SYNC):
        assert "export -n \"$key\"" in source or "export -n \"$secret_key\"" in source
        assert "MIGRATION_DB_PASSWORD" in source
        assert "VPS_PASSWORD" in source
        assert "BACKUP_ENCRYPTION_KEY" not in source
    assert "unexport_sensitive_environment" in RUN
    assert "unexport_sensitive_environment" in SYNC


def test_release_parent_remains_traversable_by_the_runtime_user() -> None:
    assert "install -d -o root -g root -m 755 /opt/eden/releases" in DEPLOY
    assert "install -d -m 750 '${remote_release}' /opt/eden/releases" not in DEPLOY


def test_nginx_records_dashboard_failures_and_client_api_status_errors() -> None:
    assert "eden-dashboard.access.log" in DEPLOY
    assert "eden-dashboard.error.log" in DEPLOY
    assert "log_not_found on;" in DEPLOY
    assert "eden-api-client.access.log" in DEPLOY
    assert "eden-api-client.error.log" in DEPLOY
    assert "access_log /var/log/nginx/eden-api.access.log combined;" in DEPLOY


def test_phase2_soak_has_dedicated_service_timer_and_evidence_gate() -> None:
    assert "scripts/phase2_soak.py status --path \"$evidence_path\"" in DEPLOY
    assert "scripts/phase2_soak.py sample --path /opt/eden/phase2-evidence/soak.jsonl" in DEPLOY
    assert "EnvironmentFile=/opt/eden/shared/.env" in DEPLOY
    assert "eden-phase2-soak.service" in DEPLOY
    assert "eden-phase2-soak.timer" in DEPLOY
    assert "OnUnitActiveSec=5min" in DEPLOY
    assert "^SNAPSHOT_RETENTION_ENABLED=true$" in DEPLOY
