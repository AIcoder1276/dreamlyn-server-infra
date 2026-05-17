# Misc Compose Project

This compose project contains utility containers that are not part of the always-running service stack.

Currently it is used to issue or renew the public TLS certificate for the main domain and its wildcard subdomains. The certificate is created by Certbot and stored in the external Docker volume `server_certbot_data`, which is also mounted by the main Services/NGINX stack.

## Certificate Update

The domain registrar does not provide usable automated DNS API support for wildcard certificates. Because of that, wildcard renewal uses Certbot's manual DNS-01 flow and requires manually creating `_acme-challenge` TXT records during each update.

Do not run this while expecting a fully unattended renewal. Certbot will pause and print the TXT value that must be added in DNS.

## Prerequisites

1. Docker and Docker Compose are available on the host.
2. The external Docker volume exists:

   ```powershell
   docker volume ls
   ```

   The list must include `server_certbot_data`.

3. `Misc/.env` exists and contains:

   ```env
   CERT_DOMAIN=example.com
   CERT_ADMIN_EMAIL=admin@example.com
   ```

   Replace the values with the real domain and certificate administrator email.

4. You have registrar/DNS panel access for `CERT_DOMAIN`.

## Step-by-Step Renewal

1. Open a terminal in the repository `infra` directory.

2. Run Certbot through the Misc compose project:

   ```powershell
   docker compose -f Misc\docker-compose.yml --env-file Misc\.env run --rm certbot
   ```

3. Certbot will request DNS TXT records for the configured names:

   ```text
   CERT_DOMAIN
   *.CERT_DOMAIN
   ```

   For DNS-01 validation, both prompts normally use the same record name:

   ```text
   _acme-challenge.CERT_DOMAIN
   ```

4. In the registrar/DNS panel, create the TXT record exactly as Certbot prints it.

   If Certbot asks for two TXT values for `_acme-challenge.CERT_DOMAIN`, keep both values at the same time. Do not replace the first value with the second unless Certbot explicitly asks you to remove it.

5. Wait for DNS propagation.

   Check from a public resolver:

   ```powershell
   nslookup -type=TXT _acme-challenge.example.com 8.8.8.8
   ```

   Replace `example.com` with `CERT_DOMAIN`. Continue only after the expected TXT value is visible.

6. Return to the Certbot prompt and press Enter.

7. Repeat the DNS creation and propagation check for every Certbot challenge prompt.

8. When Certbot completes successfully, the renewed certificate is written under:

   ```text
   /etc/letsencrypt/live/CERT_DOMAIN/
   ```

   inside the `server_certbot_data` Docker volume.

9. Restart or recreate the NGINX service from the Services compose project so it reads the renewed certificate:

   ```powershell
   docker compose -f Services\docker-compose.yml --env-file Services\.env restart server
   ```

   If the service stack is not running, start it normally from the Services project instead.

10. Verify the certificate from outside the host:

    ```powershell
    openssl s_client -connect example.com:443 -servername example.com
    ```

    Replace `example.com` with `CERT_DOMAIN` and check the `notAfter` date.

## Notes

- The compose command uses `certonly --manual --preferred-challenges dns` and requests both `CERT_DOMAIN` and `*.CERT_DOMAIN`.
- Manual DNS records are temporary validation records. Remove stale `_acme-challenge` TXT values after the certificate has been issued, unless another active renewal is using them.
- Do not delete `server_certbot_data`; it contains the live certificate files consumed by NGINX.
- This project is for certificate maintenance only. It should not be part of the normal application startup path.
