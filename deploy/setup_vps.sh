#!/usr/bin/env bash
# Run ON VPS once to bootstrap: install deps, venv, systemd, nginx, SSL cert.
# Assumes rsync already pushed web/ and deploy/ to /opt/lockdown-web/
# Assumes .env has been provisioned to /opt/lockdown-web/.env
set -euo pipefail

APP_DIR=/opt/lockdown-web
DOMAIN=<YOUR_DOMAIN>
HTTPS_PORT=4443

[ -d "$APP_DIR/web" ] || { echo "ERROR: $APP_DIR/web missing. rsync first."; exit 1; }
[ -f "$APP_DIR/.env" ] || { echo "ERROR: $APP_DIR/.env missing. Provision secrets first."; exit 1; }

echo "==> apt install"
apt update
DEBIAN_FRONTEND=noninteractive apt install -y python3-venv nginx certbot rsync

echo "==> venv + pip"
cd "$APP_DIR"
if [ ! -d .venv ]; then
    python3 -m venv .venv
fi
.venv/bin/pip install --upgrade pip --quiet
.venv/bin/pip install -r web/requirements.txt --quiet

echo "==> systemd web-admin.service"
cp deploy/web-admin.service /etc/systemd/system/web-admin.service
systemctl daemon-reload
systemctl enable web-admin
systemctl restart web-admin
sleep 2
systemctl is-active web-admin || { journalctl -u web-admin -n 30 --no-pager; exit 1; }

echo "==> certbot standalone (needs port 80)"
mkdir -p /var/www/certbot
if [ ! -d /etc/letsencrypt/live/${DOMAIN} ]; then
    systemctl stop nginx 2>/dev/null || true
    certbot certonly --standalone \
        -d "$DOMAIN" \
        --non-interactive --agree-tos \
        --register-unsafely-without-email \
        --preferred-challenges http
fi

echo "==> nginx config"
cp deploy/nginx-lockdown.conf /etc/nginx/sites-available/lockdown
ln -sf /etc/nginx/sites-available/lockdown /etc/nginx/sites-enabled/lockdown
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable nginx
systemctl restart nginx

echo "==> firewall (best-effort)"
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
    ufw allow 80/tcp
    ufw allow ${HTTPS_PORT}/tcp
fi

echo ""
echo "✓ setup done"
echo "  web-admin: $(systemctl is-active web-admin)"
echo "  nginx:     $(systemctl is-active nginx)"
echo "  URL:       https://${DOMAIN}:${HTTPS_PORT}"
