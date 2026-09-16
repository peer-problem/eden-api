# Production process layout

The VPS runs the public API and the collection scheduler as two systemd
services on the same host. `.ops/deploy.sh deploy` is unchanged: it stops,
switches and restarts `eden-api`, and the unit dependencies below carry
`eden-scheduler` along.

| Unit | Role | Memory cap |
| --- | --- | --- |
| `eden-api.service` | uvicorn, public API, pilot usage recording (`SCHEDULER_ENABLED=false`) | 400M |
| `eden-scheduler.service` | `python -m app.scheduler`, same jobs and locks as before | 600M |
| `eden-api-watchdog.timer` | every 30 s: kills eden-api when loopback `/internal/health` fails twice | – |

`eden-scheduler` is `PartOf=` and `After=` `eden-api`, and `eden-api` `Wants=`
`eden-scheduler`, so every `systemctl stop|start|restart eden-api` in the deploy
and schema-finalization paths applies to both, in the right order. A scheduler
crash or memory-cap kill restarts only the scheduler.

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

## Rollback to the in-process scheduler

```bash
systemctl disable --now eden-scheduler.service
rm -f /etc/systemd/system/eden-api.service.d/30-scheduler-split.conf /opt/eden/shared/api.env
sed -i 's/^MemoryMax=.*/MemoryMax=900M/' /etc/systemd/system/eden-api.service.d/20-memory-guard.conf
systemctl daemon-reload && systemctl restart eden-api
```
