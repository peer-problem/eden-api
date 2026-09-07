from __future__ import annotations

import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OPS_SCRIPT = REPOSITORY_ROOT / ".ops" / "deploy.sh"
DEPLOY_SUPPORT_SCRIPT = REPOSITORY_ROOT / "scripts" / "deploy_support.sh"
DEPLOY_REMOTE_SCRIPT = REPOSITORY_ROOT / "scripts" / "deploy_remote.sh"


def _deploy_source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in (OPS_SCRIPT, DEPLOY_SUPPORT_SCRIPT, DEPLOY_REMOTE_SCRIPT)
    )


def test_operations_shell_scripts_have_valid_syntax() -> None:
    for script in (
        OPS_SCRIPT,
        REPOSITORY_ROOT / ".ops" / "run.sh",
        REPOSITORY_ROOT / ".ops" / "sync-env.sh",
        DEPLOY_SUPPORT_SCRIPT,
        DEPLOY_REMOTE_SCRIPT,
    ):
        result = subprocess.run(  # noqa: S603
            ["/bin/bash", "-n", str(script)],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        assert result.returncode == 0, f"{script}: {result.stderr}"


def test_database_backup_and_restore_commands_are_disabled() -> None:
    for command in ("backup", "restore"):
        result = subprocess.run(  # noqa: S603
            ["/bin/bash", str(OPS_SCRIPT), command],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        assert result.returncode == 2
        assert "disabled by project policy" in result.stderr

    source = _deploy_source()
    assert "mariadb-dump" not in source
    assert "backup_database" not in source
    assert "restore_database" not in source
    assert "/opt/eden/backups" not in source
    assert "BACKUP_ENCRYPTION_KEY" not in source


def test_deploy_applies_expand_migration_without_database_backup() -> None:
    source = _deploy_source()
    stop_index = source.index("systemctl stop eden-api")
    migration_index = source.index("uv run alembic upgrade 20260829_0008", stop_index)

    assert ': "${MIGRATION_DB_USER:?Missing MIGRATION_DB_USER}"' in source
    assert ': "${MIGRATION_DB_PASSWORD:?Missing MIGRATION_DB_PASSWORD}"' in source
    assert stop_index < migration_index
    assert "uv run alembic upgrade 20260829_0008" in source
    assert "uv run alembic heads" in source
    assert "uv run alembic current" in source
    assert "uv run alembic check" not in source
    assert "uv run alembic upgrade head" not in source
    assert "mariadb-dump" not in source
    assert "backup_database" not in source
    assert "BACKUP_ENCRYPTION_KEY" not in source


def test_deploy_write_scripts_use_the_distinct_ingestion_engine() -> None:
    deploy_source = DEPLOY_REMOTE_SCRIPT.read_text(encoding="utf-8")
    seed_index = deploy_source.index("uv run python scripts/seed_reference.py")
    runtime_restore_index = deploy_source.rindex(
        'export DB_USER="$runtime_user"', 0, seed_index
    )

    assert runtime_restore_index < seed_index
    assert 'export DB_USER="$INGESTION_DB_USER"' not in deploy_source
    for script_name in (
        "seed_reference.py",
        "import_mois_areas.py",
        "import_kto_inbound.py",
        "import_keta_notices.py",
        "publish_bounded_recommendations.py",
    ):
        script_source = (REPOSITORY_ROOT / "scripts" / script_name).read_text(
            encoding="utf-8"
        )
        assert "create_scheduler_database_engine" in script_source


def test_contract_finalizer_requires_soak_and_rolls_back_a_failed_contract() -> None:
    source = _deploy_source()
    status_index = source.index("scripts/phase1_soak.py status")
    stop_index = source.index("systemctl stop eden-api")
    contract_index = source.index("alembic upgrade 20260829_0007")

    assert status_index < stop_index < contract_index
    assert "alembic downgrade 20260829_0008" in source
    assert "scripts/phase1_soak.py sample" in source
    assert source.count("scripts/phase1_soak.py status") >= 2
    assert "scripts/phase1_soak.py warmup" in source
    assert "systemctl stop eden-phase1-soak.timer" in source
    assert source.count("enable --now eden-phase1-soak.timer") >= 2
    assert "SNAPSHOT_RETENTION_ENABLED=true" in source
    assert "EnvironmentFile=/opt/eden/shared/phase1-contract.env" in source
    assert "30-phase1-contract.conf" in source
    assert "backup_database" not in source


def test_deploy_recovers_previous_release_when_remote_activation_fails() -> None:
    source = _deploy_source()

    assert 'previous_release="$(readlink -f /opt/eden/current' in source
    assert "trap recover_previous_release EXIT" in source
    assert 'ln -sfn "$previous_release" /opt/eden/current' in source
    assert source.count("20-phase1-baseline.conf") >= 3
    assert source.count("baseline.env") >= 4
    assert source.count("enable --now eden-phase1-soak.timer") >= 2
    assert "deployment_complete=1" in source


def test_deploy_stops_sampling_and_warms_the_scheduler_on_process_before_restarting_it() -> None:
    source = _deploy_source()
    timer_stop_index = source.index("systemctl stop eden-phase1-soak.timer")
    api_stop_index = source.index("systemctl stop eden-api")
    warmup_index = source.index("scripts/phase1_soak.py warmup")
    timer_start_index = source.rindex("enable --now eden-phase1-soak.timer")

    assert timer_stop_index < api_stop_index
    assert warmup_index < timer_start_index


def test_deploy_publishes_the_bounded_recommendation_product_before_activation() -> None:
    source = _deploy_source()
    publish_index = source.index("scripts/publish_bounded_recommendations.py")
    activate_index = source.index('ln -sfn "$release" /opt/eden/current')
    baseline_index = source.index("scripts/phase1_soak.py baseline")

    assert publish_index < activate_index < baseline_index


def test_nginx_release_config_applies_body_concurrency_and_timeout_limits() -> None:
    source = _deploy_source()

    assert "limit_conn_zone $binary_remote_addr zone=eden_per_ip:10m;" in source
    assert source.count("client_max_body_size 64k;") == 2
    assert source.count("limit_conn eden_per_ip 20;") >= 1
    assert source.count("proxy_connect_timeout 5s;") >= 2
    assert source.count("proxy_read_timeout 30s;") >= 2


def test_internal_sprint_documents_are_ignored_and_excluded_from_release() -> None:
    ignore_entries = {
        line.strip()
        for line in (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    }
    deploy_source = _deploy_source()

    assert ".agents/" in ignore_entries
    assert "AGENTS.md" in ignore_entries
    assert "--exclude '.agents/'" in deploy_source


def test_operations_directory_contains_only_the_three_authorized_entrypoints() -> None:
    assert sorted(path.name for path in (REPOSITORY_ROOT / ".ops").iterdir()) == [
        "deploy.sh",
        "run.sh",
        "sync-env.sh",
    ]


def test_deploy_has_dedicated_user_and_security_boundaries() -> None:
    source = _deploy_source()

    assert ': "${ENVIRONMENT:?Missing ENVIRONMENT}"' in source
    assert '[[ "$ENVIRONMENT" == "production" ]]' in source
    assert 'settings.ENVIRONMENT != "production"' in source
    assert "useradd --system" in source
    assert "User=eden" in source
    assert "Group=eden" in source
    assert "NoNewPrivileges=true" in source
    assert "ProtectSystem=strict" in source
    assert "DB_SSL_CA" in source
    assert "ssl-verify-server-cert" in source
    assert "DB_NETWORK_MODE" in source
    assert "DB_ALLOWED_CIDRS" in source
    assert "scripts/verify_db_security.py firewall" in source
    assert "SHOW STATUS LIKE 'Ssl_cipher'" in source
    assert "DB runtime, ingestion, and migration users must be distinct." in source
    assert (
        "UnsetEnvironment=MIGRATION_DB_USER MIGRATION_DB_PASSWORD VPS_PASSWORD"
    ) in source
    assert "chown root:eden /opt/eden/shared/.env" in source
    assert "chmod 640 /opt/eden/shared/.env" in source
    assert "/opt/eden/shared/migration.env" in source
    assert 'root:root:600' in source
    assert "ReadWritePaths=/opt/eden/shared" not in source
    assert "scripts/verify_db_security.py grants" in source
    assert '--role ingestion --database "$DB_NAME"' in source


def test_nginx_without_a_certificate_is_fail_closed() -> None:
    source = _deploy_source()

    assert "if [[ ! -r \"$certificate\" || ! -r \"$certificate_key\" ]]" in source
    assert "location / { return 503; }" in source
    assert "TLS certificate is missing; deployment is fail-closed." in source
    assert "openssl x509 -in \"$certificate\" -noout -checkend 86400" in source
    assert "location / { return 308 https://$host$request_uri; }" in source


def test_missing_certificate_rollback_preserves_fail_closed_nginx() -> None:
    source = _deploy_source()

    assert "preserve_fail_closed=0" in source
    assert "if (( preserve_fail_closed == 0 ))" in source
    missing_certificate = source.index('if [[ ! -r "$certificate" || ! -r "$certificate_key" ]]')
    preserve = source.index("preserve_fail_closed=1", missing_certificate)
    validation = source.index("if ! nginx -t", preserve)
    stop = source.index("systemctl stop nginx", validation)
    failure = source.index(
        'echo "TLS certificate is missing; deployment is fail-closed."',
        preserve,
    )
    assert missing_certificate < preserve < validation < stop < failure


def test_deploy_uses_fingerprint_pinned_ssh_and_excludes_runtime_secrets() -> None:
    source = _deploy_source()

    assert "ssh-keyscan" in source
    assert "VPS_HOST_FINGERPRINT" in source
    assert "StrictHostKeyChecking=yes" in source
    assert "StrictHostKeyChecking=accept-new" not in source
    assert "--exclude '.env'" in source
    assert "--exclude '.agents/'" in source
    assert "--exclude 'tmp/'" in source
    assert "--exclude 'cache/'" in source
    assert "--exclude '.playwright-cli/'" in source
    assert "--exclude 'playwright-report/'" in source
    assert "--exclude 'test-results/'" in source
    assert "--exclude '.pytest_cache/'" in source


def test_sync_env_allowlist_contains_db_security_keys_without_printing_values() -> None:
    source = (REPOSITORY_ROOT / ".ops" / "sync-env.sh").read_text(encoding="utf-8")
    allowlist = source.split("allowed=(", 1)[1].split(")", 1)[0]

    assert "DB_SSL_CA" in source
    assert "DB_SSL_VERIFY_CERT" in source
    assert "DB_NETWORK_MODE" in source
    assert "DB_ALLOWED_CIDRS" in source
    assert "DB_PASSWORD" in source
    assert "cut -d= -f1" in source
    assert "StrictHostKeyChecking=yes" in source
    assert "StrictHostKeyChecking=accept-new" not in source
    assert "chmod 640 /opt/eden/shared/.env" in source
    assert "chown root:eden /opt/eden/shared/.env" in source
    assert "INGESTION_DB_USER" in allowlist
    assert "INGESTION_DB_PASSWORD" in allowlist
    assert "updates.pop(key)" in source
    assert 'migration_path = target.parent / "migration.env"' in source
    assert "os.chown(temporary, 0, 0)" in source
    assert "MIGRATION_DB_USER" in allowlist
    assert "MIGRATION_DB_PASSWORD" in allowlist
    assert "BACKUP_ENCRYPTION_KEY" not in source
    assert "BACKUP_DIR" not in source
    assert "if key in forbidden" in source
    assert "groupadd --system eden" in source
    assert "install -d -o root -g eden -m 750 /opt/eden/shared" in source
    assert "target.read_text().splitlines() if target.is_file() else []" in source


def test_deploy_builds_and_installs_the_dashboard_artifact() -> None:
    source = _deploy_source()

    assert "npm ci --prefix dashboard" in source
    assert "npm run build --prefix dashboard" in source
    assert "dashboard/dist/index.html" in source
    assert "dashboard_root=\"/var/www/eden-dashboard\"" in source
    assert "ln -sfn \"$dashboard_release\" \"$dashboard_root/current\"" in source
    assert "alias /var/www/eden-dashboard/current/;" in source
    assert "try_files $uri $uri/ /dashboard/index.html;" in source


def test_deploy_exposes_a_seven_day_soak_evidence_gate() -> None:
    source = _deploy_source()

    assert "soak-7d|phase2-soak" in source
    assert "scripts/phase2_soak.py status" in source
    assert "/opt/eden/phase2-evidence/soak.jsonl" in source


def test_deploy_installs_the_daily_phase2_pilot_report_timer() -> None:
    source = _deploy_source()

    assert "eden-phase2-pilot-report.service" in source
    assert "eden-phase2-pilot-report.timer" in source
    assert (
        "scripts/phase2_pilot.py report --output "
        "/opt/eden/phase2-evidence/pilot-report.json"
    ) in source
    assert "OnCalendar=*-*-* 04:15:00" in source
    assert "Persistent=true" in source
    assert "ReadWritePaths=/opt/eden/phase2-evidence" in source
    assert "systemctl start eden-phase2-pilot-report.service" in source
    assert "eden-db-maintenance-report.timer" in source


def test_deploy_public_smoke_uses_required_trend_parameters_and_eight_route_warmup() -> None:
    source = _deploy_source()
    soak_source = (REPOSITORY_ROOT / "scripts" / "phase1_soak.py").read_text(encoding="utf-8")

    assert "keyword=%EC%A0%9C%EC%A3%BC&period=7d&time_unit=day&limit=1" in source
    assert "scripts/phase1_soak.py warmup" in source
    assert "def probe_public_routes()" in soak_source
    assert '"trends", f"/v1/trends?{trend_query}"' in soak_source
    assert '"/v1/regions/{encoded_area}/insights?period=90d&visitor_type=all"' in soak_source
    assert '"/v1/places/{encoded_place}?lang=en&radius_m=5000&related_limit=50"' in soak_source
    assert '"/v1/forecasts/visitors?area_code={encoded_area}&days=30"' in soak_source
    assert '"/v1/visitors/timeseries?area_code={encoded_area}"' in soak_source
    assert '"inbound_markets", f"/v1/markets/inbound?{inbound_query}"' in soak_source
    assert '"/v1/markets/{encoded_country}/alerts?limit=100&source_scope=all"' in soak_source
    assert '"/v1/recommendations/destinations"' in soak_source
