#!/bin/sh
set -eu

: "${DOMAIN:?DOMAIN must be set}"

certificate_dir="/etc/letsencrypt/live/${DOMAIN}"
fullchain="${certificate_dir}/fullchain.pem"
private_key="${certificate_dir}/privkey.pem"
rendered_dir="/etc/nginx/rendered"
active_config="/etc/nginx/conf.d/default.conf"

if [ -r "$fullchain" ] && [ -r "$private_key" ]; then
    mode_template="https.conf.template"
    echo "nginx: TLS certificate found; enabling HTTPS services"
else
    mode_template="http.conf.template"
    echo "nginx: TLS certificate not found; exposing the HTTP landing page only"
fi

mkdir -p "$rendered_dir"
envsubst '${DOMAIN}' \
    < /etc/nginx/templates/common.conf.template \
    > "$rendered_dir/common.conf"
envsubst '${DOMAIN}' \
    < "/etc/nginx/templates/${mode_template}" \
    > "$rendered_dir/active.conf"

printf '%s\n' \
    'include /etc/nginx/rendered/common.conf;' \
    'include /etc/nginx/rendered/active.conf;' \
    > "$active_config"

nginx -t
exec nginx -g 'daemon off;'
