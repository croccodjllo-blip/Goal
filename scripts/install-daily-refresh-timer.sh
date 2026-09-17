#!/usr/bin/env bash
# Install systemd timer for daily fixtures + team-stats refresh on the Goal VPS.
# Usage (on VPS as root): bash scripts/install-daily-refresh-timer.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
install -m 644 "$ROOT/scripts/goal-xg-daily-refresh.service" /etc/systemd/system/goal-xg-daily-refresh.service
install -m 644 "$ROOT/scripts/goal-xg-daily-refresh.timer" /etc/systemd/system/goal-xg-daily-refresh.timer
systemctl daemon-reload
systemctl enable --now goal-xg-daily-refresh.timer
systemctl list-timers goal-xg-daily-refresh.timer --no-pager
echo "Smoke: systemctl start goal-xg-daily-refresh.service && journalctl -u goal-xg-daily-refresh -n 40 --no-pager"
