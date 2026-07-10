# Nginx Gateway

The gateway selects its mode whenever the `server` container starts.

- If both `/etc/letsencrypt/live/$DOMAIN/fullchain.pem` and `privkey.pem` are readable, all configured services are exposed over HTTPS and HTTP requests redirect to HTTPS.
- If either certificate file is unavailable, only the root-domain landing page is exposed over HTTP. Other hosts are rejected with status `444`.
- Docker service names are resolved when requests arrive. An unavailable service returns `503 Service Unavailable` without preventing nginx or unrelated services from working.

After issuing or renewing a certificate, restart the gateway so it selects HTTPS mode and loads the new files:

```sh
docker compose -f services/docker-compose.yml --env-file services/.env restart server
```
