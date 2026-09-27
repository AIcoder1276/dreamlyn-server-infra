#!/usr/bin/env bash
# Interactively issue or renew the wildcard certificate (DNS-01, manual TXT records),
# then restart the nginx gateway so it loads the new certificate.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SERVICE="certbot"

MISC_COMPOSE=(docker compose -f "${SCRIPT_DIR}/docker-compose.yml")
[[ -f "${SCRIPT_DIR}/.env" ]] && MISC_COMPOSE+=(--env-file "${SCRIPT_DIR}/.env")

SERVICES_COMPOSE=(docker compose -f "${REPO_ROOT}/services/docker-compose.yml" --env-file "${REPO_ROOT}/services/.env")

read_env() {
    # read_env FILE KEY: print KEY's value from a dotenv file, quotes stripped.
    [[ -f "$1" ]] || return 0
    grep -E "^$2=" "$1" | tail -n1 | cut -d= -f2- | tr -d '"'"'"
}

DOMAIN="${CERT_DOMAIN:-$(read_env "${SCRIPT_DIR}/.env" CERT_DOMAIN)}"
DOMAIN="${DOMAIN:-$(read_env "${REPO_ROOT}/services/.env" CERT_DOMAIN)}"
EMAIL="${CERT_EMAIL:-$(read_env "${SCRIPT_DIR}/.env" CERT_EMAIL)}"

if [[ -z "${DOMAIN}" ]]; then
    read -rp "Domain (without *.): " DOMAIN
fi
if [[ -z "${EMAIL}" ]]; then
    read -rp "Contact email for Let's Encrypt: " EMAIL
fi

echo
echo "Requesting certificate for: ${DOMAIN}, *.${DOMAIN}"
echo "Certbot will pause and show TXT records for _acme-challenge.${DOMAIN}."
echo "Add each record at the registrar, wait for it to propagate, then press Enter."
echo "Check propagation from another terminal:"
echo "  dig +short TXT _acme-challenge.${DOMAIN} @1.1.1.1"
echo
