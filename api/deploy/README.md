# Production process layout

The VPS runs the public API and the collection scheduler as two systemd
services on the same host. `.ops/deploy.sh deploy` is unchanged: it stops,
switches and restarts `eden-api`, and the unit dependencies below carry
`eden-scheduler` along.

| Unit | Role | Memory cap |
| --- | --- | --- |
| `eden-api.service` | uvicorn, public API, pilot usage recording (`SCHEDULER_ENABLED=false`) | 400M |
| `eden-scheduler.service` | `python -m app.scheduler`, same jobs and locks as before | 1G |
| `eden-api-watchdog.timer` | every 30 s: kills eden-api when loopback `/internal/health` fails twice | – |

`eden-scheduler` is `PartOf=` and `After=` `eden-api`, and `eden-api` `Wants=`
`eden-scheduler`, so every `systemctl stop|start|restart eden-api` in the deploy
and schema-finalization paths applies to both, in the right order. A scheduler
crash or memory-cap kill restarts only the scheduler.

## Shared MariaDB memory budget

`mariadb/60-shared-vps.cnf` records the shared VPS configuration at
`/etc/mysql/mariadb.conf.d/60-shared-vps.cnf`. The InnoDB cache uses 512 MiB,
with a 16 MiB MyISAM key cache. These are cache budgets; MariaDB also needs
memory for connections and internal structures. Keep the cache independent
of total host RAM. Normal API deployments preserve the existing DB settings.

Observation retention scans candidate IDs in pages of 500 and checks snapshot
protection in Python. It never expands all protected IDs into a SQL `NOT IN`
clause, which previously exceeded MariaDB's placeholder limit and accumulated
memory on repeated failures.

## One-time installation (VPS, as root)

The files ship with every release under `api/deploy/`. Install or refresh them:

```bash
cd /opt/eden/current/api/deploy
install -o root -g eden -m 640 systemd/api.env /opt/eden/shared/api.env
install -d -m 755 /etc/systemd/system/eden-api.service.d
install -m 644 systemd/eden-api.service.d/*.conf /etc/systemd/system/eden-api.service.d/
install -m 644 systemd/eden-scheduler.service systemd/eden-api-watchdog.service systemd/eden-api-watchdog.timer /etc/systemd/system/
install -m 755 bin/eden-api-watchdog /usr/local/sbin/eden-api-watchdog
systemctl daemon-reload
systemctl enable --now eden-api-watchdog.timer eden-scheduler.service
systemctl restart eden-api          # applies api.env; restarts eden-scheduler via PartOf
```

Check: `systemctl show eden-scheduler -p PartOf` prints `eden-api.service`,
`curl -s 127.0.0.1:8000/internal/readiness` shows `"scheduler_enabled":false`,
and `journalctl -u eden-scheduler` shows `scheduler_process_started`.

## Soak evidence with two services

`eden-phase1-soak.timer` and `eden-phase2-soak.timer` keep sampling every five
minutes; each sample now records `eden-scheduler` next to the required services
and derives `scheduler_enabled` from the API readiness flag plus the service
state. Both evaluators restart their window whenever the release symlink
changes, so a deploy resets the 24-hour and 7-day clocks.

The Phase 1 gate needs probes from a scheduler-off phase inside the same
window. With the split that phase is "scheduler service stopped, API serving":

```bash
systemctl stop eden-scheduler            # eden-api keeps serving
systemd-run --wait --pipe --collect --uid=eden --gid=eden \
  -p WorkingDirectory=/opt/eden/current/api -p EnvironmentFile=/opt/eden/shared/.env \
  -p "UnsetEnvironment=MIGRATION_DB_USER MIGRATION_DB_PASSWORD VPS_PASSWORD" \
  /opt/eden/current/api/.venv/bin/python -m app.operations phase1-soak baseline --iterations 20
systemctl start eden-scheduler
```

Run it right after a timer tick so the two-minute gap holds no timer sample. A
`systemctl stop`/`start` pair does not count as a restart, but a Phase 2 sample
taken while the service is stopped is a `scheduler_disabled` violation that
stays in the sliding window for seven days.

## Rollback to the in-process scheduler

```bash
systemctl disable --now eden-scheduler.service
rm -f /etc/systemd/system/eden-api.service.d/30-scheduler-split.conf /opt/eden/shared/api.env
sed -i 's/^MemoryMax=.*/MemoryMax=900M/' /etc/systemd/system/eden-api.service.d/20-memory-guard.conf
systemctl daemon-reload && systemctl restart eden-api
```
