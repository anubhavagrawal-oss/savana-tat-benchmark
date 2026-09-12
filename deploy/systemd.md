# systemd units (alternative to cron)

Preferred over cron on a real server: you get `journalctl` history, automatic
restart semantics, and `Persistent=true` so a sweep missed while the box was
down runs when it comes back — which cron will not do.

## /etc/systemd/system/tatbench@.service

```ini
[Unit]
Description=tatbench TAT sweep (%i)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=tatbench
WorkingDirectory=/opt/tatbench
ExecStartPre=/usr/bin/python3 run.py --preflight --brands %i
ExecStart=/usr/bin/python3 run.py --brands %i
# A sweep should never outlive its night. If it does, something is wrong and
# you want it dead before it collides with the next day's run.
TimeoutStartSec=5h
Nice=10
# Harmless hardening - this process only needs to read its own dir and write data/
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/opt/tatbench/data /opt/tatbench/reports

[Install]
WantedBy=multi-user.target
```

`ExecStartPre` is the important line: preflight runs first, and a failure there
aborts before the sweep starts. A scheduled job that cheerfully produces an
empty file is worse than one that refuses to run.

## /etc/systemd/system/tatbench@.timer

```ini
[Unit]
Description=Weekly TAT sweep for %i

[Timer]
# Set per brand at enable time; see the day map below.
OnCalendar=Mon *-*-* 01:30:00 Asia/Kolkata
Persistent=true
RandomizedDelaySec=900

[Install]
WantedBy=timers.target
```

`RandomizedDelaySec` spreads the start so you are not hitting anyone at exactly
01:30:00 every week — a pattern that is trivially fingerprintable.

## Enable

```bash
sudo systemctl daemon-reload
for b in savana myntra nykaafashion ajio meesho; do
  sudo systemctl enable --now tatbench@$b.timer
done
# then edit each timer's OnCalendar to its own night:
#   savana Mon · myntra Tue · nykaafashion Wed · ajio Thu · meesho Fri
sudo systemctl list-timers 'tatbench@*'
```

## Watching a run

```bash
journalctl -u tatbench@savana -f          # live
journalctl -u tatbench@savana --since today
systemctl status tatbench@myntra
```

## Weekly report

```ini
# /etc/systemd/system/tatbench-report.service
[Service]
Type=oneshot
User=tatbench
WorkingDirectory=/opt/tatbench
ExecStart=/bin/sh -c '/usr/bin/python3 analyse.py --detail --out /opt/tatbench/reports/$(date +%%F).md'
```

```ini
# /etc/systemd/system/tatbench-report.timer
[Timer]
OnCalendar=Sat *-*-* 07:00:00 Asia/Kolkata
Persistent=true
```
