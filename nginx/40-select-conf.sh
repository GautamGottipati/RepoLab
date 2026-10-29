#!/bin/sh
# Runs inside the nginx image before its envsubst step (20-envsubst-on-templates).
# Picks HTTPS once a certificate for $REPOLAB_DOMAIN exists, HTTP otherwise, so
# the site works by IP before DNS and TLS are set up.
set -eu

mkdir -p /etc/nginx/templates
rm -f /etc/nginx/conf.d/default.conf /etc/nginx/templates/*.template

if [ -f "/etc/letsencrypt/live/${REPOLAB_DOMAIN}/fullchain.pem" ]; then
    mode=https
else
    mode=http
fi
cp "/etc/repolab/nginx/${mode}.conf.template" /etc/nginx/templates/repolab.conf.template
echo "repolab: serving ${mode} for ${REPOLAB_DOMAIN}"
