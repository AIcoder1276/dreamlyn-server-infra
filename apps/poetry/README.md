# Dreamlyn Poetry Production Deployment

This directory contains only the runtime declaration and persistent data locations for the Poetry
application. Backend and frontend source code stay in separate production checkouts and are supplied
to Docker Compose through build-context variables.

## Runtime topology

```text
poetry.${DOMAIN}       -> server/nginx -> poetry-frontend:8080
api-poetry.${DOMAIN}   -> server/nginx -> poetry-backend:8000
admin-poetry.${DOMAIN} -> Authelia     -> poetry-backend:8000/admin/*
                                           |
                                           +-> app-poetry-postgres:5432
                                           +-> litellm-master:4000
```

All containers use the external `dreamlyn_mesh` network. Poetry container ports must not be
published on the host.

## Production files

From the infrastructure repository root:

1. Copy `apps/.env.example` to `apps/.env` and replace every example path, domain, password and
   secret. `POETRY_DATABASE_URL` must contain the same database credentials as `POETRY_DB_*`; URL
   encode reserved password characters in the URL.
2. Copy `apps/poetry/frontend/config.yaml.example` to
   `apps/poetry/frontend/config.yaml` and set the public API URL:

   ```yaml
   apiBaseUrl: https://api-poetry.example.com
   ```

3. In `services/api/ai/adapters/music/config.yml`, set the production generation range to `5..30`
   seconds, matching its committed example and the Poetry API contract.
4. Add `admin-poetry.${DOMAIN}` to the same Authelia `two_factor` access-control rule used by the
   existing protected services. Do not expose the Poetry administration routes without Authelia.

The real `.env`, frontend `config.yaml`, PostgreSQL data and database dumps are ignored by Git.

## First deployment

Run these steps on the production server only. They are not local-development commands.

1. Confirm the external network and both source checkouts:

   ```sh
   docker network inspect dreamlyn_mesh
   test -f /path/to/poetry-backend/Dockerfile
   test -f /path/to/poetry-frontend/Dockerfile
   ```

2. Record the backend and frontend Git commit hashes used for the release, set a matching
   `POETRY_RELEASE_TAG`, and validate the resolved declaration:

   ```sh
   docker compose --env-file apps/.env -f apps/docker-compose.yml config --quiet
   ```

3. Build the application images and start PostgreSQL:

   ```sh
   docker compose --env-file apps/.env -f apps/docker-compose.yml build poetry-backend poetry-frontend
   docker compose --env-file apps/.env -f apps/docker-compose.yml up -d app-poetry-postgres
   ```

4. Apply reviewed Alembic migrations as a one-shot action. Never add this command to the backend
   container startup command:

   ```sh
   docker compose --env-file apps/.env -f apps/docker-compose.yml run --rm poetry-backend \
     uv run alembic upgrade head
   ```

5. Start the application containers, restart the music adapter after changing its configuration,
   and restart the central gateway so its entrypoint renders the updated nginx template:

   ```sh
   docker compose --env-file apps/.env -f apps/docker-compose.yml up -d poetry-backend poetry-frontend
   docker compose --env-file services/.env -f services/docker-compose.yml restart adapter-music
   docker compose --env-file services/.env -f services/docker-compose.yml restart server
   ```

## Verification

Check container state before public smoke tests:

```sh
docker compose --env-file apps/.env -f apps/docker-compose.yml ps
docker compose --env-file apps/.env -f apps/docker-compose.yml exec poetry-backend \
  python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3)"
docker compose --env-file apps/.env -f apps/docker-compose.yml exec poetry-frontend \
  wget -q -O /dev/null http://127.0.0.1:8080/health/live
```

Then verify externally:

- `https://poetry.${DOMAIN}` loads the SPA and its `/config.yaml` points to the public API host.
- `https://api-poetry.${DOMAIN}/health/live` and `/health/ready` return `200`.
- `/admin/*` returns `404` on the API host.
- `https://admin-poetry.${DOMAIN}/admin/docs` requires Authelia and then the backend Basic Auth.
- Registration, login, refresh-cookie rotation and logout work from the frontend origin.
- Music generation works at both 5 and 30 seconds for MP3 and WAV.
- An audio request with a `Range` header returns `206` with `Content-Range` and `Accept-Ranges`.
- Recreating `app-poetry-postgres` does not remove users, invites or generation metadata.

## Manual backup and restore runbook

Create a custom-format dump before every migration and any risky deployment. Replace the filename
timestamp explicitly so the command does not overwrite a previous dump:

```sh
docker compose --env-file apps/.env -f apps/docker-compose.yml exec -T app-poetry-postgres \
  sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > apps/poetry/db/backups/poetry-YYYYMMDDTHHMMSSZ.dump
```

Restrict the dump permissions and verify that its archive table of contents is readable:

```sh
chmod 600 apps/poetry/db/backups/poetry-YYYYMMDDTHHMMSSZ.dump
docker compose --env-file apps/.env -f apps/docker-compose.yml exec -T app-poetry-postgres \
  pg_restore --list \
  < apps/poetry/db/backups/poetry-YYYYMMDDTHHMMSSZ.dump
```

For a restore drill, create an empty temporary database, restore the archive into it, inspect the
tables and expected row counts, then drop only that temporary database. For an actual rollback,
stop `poetry-backend`, preserve the failed database separately, restore the pre-deployment dump into
a clean database, and start the backend only after `/health/ready` succeeds. Do not use an automatic
`alembic downgrade` as the production rollback mechanism.

