#!/usr/bin/env bash
# Run as root on the box from the unpacked repo at /opt/event-radar.
set -euo pipefail
cd /opt/event-radar
id eventradar >/dev/null 2>&1 || useradd --system --home /var/lib/event-radar --shell /usr/sbin/nologin eventradar
install -d -o eventradar -g eventradar -m 750 /var/lib/event-radar
install -d -o root -g eventradar -m 750 /etc/event-radar
[ -f /etc/event-radar/env ] || install -o root -g eventradar -m 640 /dev/null /etc/event-radar/env
grep -q '^RADAR_DATA_DIR=' /etc/event-radar/env || echo 'RADAR_DATA_DIR=/var/lib/event-radar' >> /etc/event-radar/env
# The web app listens on the Tailscale address only (ADR 0002).
tailnet_ip="$(tailscale ip -4 | head -1)"
sed -i '/^RADAR_WEB_HOST=/d' /etc/event-radar/env
echo "RADAR_WEB_HOST=$tailnet_ip" >> /etc/event-radar/env
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e ".[web]"
install -m 644 deploy/systemd/event-radar.service deploy/systemd/event-radar.timer deploy/systemd/event-radar-web.service /etc/systemd/system/
systemctl daemon-reload
runuser -u eventradar -- env $(grep -v '^#' /etc/event-radar/env | xargs) .venv/bin/event-radar seed
systemctl enable --now event-radar.timer
systemctl enable event-radar-web.service
systemctl restart event-radar-web.service
echo "Web app: http://$(tailscale status --self --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))'):8010/"
