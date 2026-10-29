#!/usr/bin/env bash
# Obtain the first Let's Encrypt certificate for $REPOLAB_DOMAIN, then switch
# nginx to HTTPS.
#
#   bash scripts/deploy/init_tls.sh
#
# Needs: the stack running (nginx serves the ACME challenge over HTTP), a DNS A
# record for REPOLAB_DOMAIN pointing at this VM, port 80 open, and
# LETSENCRYPT_EMAIL in .env. Renewal is automatic (repolab-certbot-renew.timer).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

value() { grep -E "^$1=" .env | tail -n1 | cut -d= -f2- | tr -d '"'"'"; }
DOMAIN="$(value REPOLAB_DOMAIN)"
EMAIL="$(value LETSENCRYPT_EMAIL)"

if [ -z "$DOMAIN" ] || [ -z "$EMAIL" ]; then
    echo "set REPOLAB_DOMAIN and LETSENCRYPT_EMAIL in .env first" >&2
    exit 1
fi
if [[ "$DOMAIN" =~ ^[0-9.]+$ ]]; then
    echo "REPOLAB_DOMAIN is an IP address; Let's Encrypt needs a domain name" >&2
    exit 1
fi

compose() { docker compose -f docker-compose.prod.yml "$@"; }

compose --profile tls run --rm certbot certonly \
    --webroot -w /var/www/acme \
    -d "$DOMAIN" \
    --email "$EMAIL" --agree-tos --no-eff-email --non-interactive

# The selector picks HTTPS at container start, now that the certificate exists.
compose up -d --force-recreate nginx
echo "TLS enabled for https://${DOMAIN}"
echo "Set PUBLIC_URL=https://${DOMAIN} and RUNTIME_PROXY_SCHEME=https in .env, then:"
echo "  docker compose -f docker-compose.prod.yml up -d --build"
