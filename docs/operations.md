# Operations guide

This guide covers the single-process SQLite deployment supported by the project today. Keep the
database and `data/uploads/` private: both can contain personal financial information.

## Production checklist

1. Set `APP_ENV=production`.
2. Generate a unique `SECRET_KEY` of at least 32 characters and store it in the deployment's secret
   manager, never in an image or Git.
3. Set `TRUSTED_HOSTS` to a JSON list containing the public hostname, for example
   `["money.example.com"]`. Wildcards are rejected.
4. Terminate HTTPS at the application host or a trusted reverse proxy. Production session and CSRF
   cookies are marked `Secure`.
5. Mount persistent private storage at `/app/data`; do not run two application processes against
   the same SQLite file.
6. Run `alembic upgrade head` before starting the new application image.
7. Verify `GET /health` returns `{"status":"ok"}` and inspect logs for the same request ID returned
   in the `X-Request-ID` header.

Build the small production image with:

```powershell
docker build --target prod -t where-is-my-money:prod .
```

The production target contains application dependencies, Alembic migrations, and Tesseract. It
does not contain tests, Ruff, Playwright, Chromium, or test fixtures.

## SQLite backup

Back up both the database and retained uploads. For a host-run development server, stop it with
Control+C first, then run:

```powershell
New-Item -ItemType Directory -Force backups | Out-Null
uv run python -c "import sqlite3; s=sqlite3.connect('data/where-is-my-money.db'); d=sqlite3.connect('backups/where-is-my-money.db'); s.backup(d); d.close(); s.close()"
Compress-Archive -Path data/uploads -DestinationPath backups/uploads.zip -Force
```

If the app runs only through Compose's named volume, copy the stopped container's data directory:

```powershell
docker compose stop web
New-Item -ItemType Directory -Force backups | Out-Null
docker compose cp web:/app/data backups/container-data
docker compose start web
```

Do not copy a live SQLite database file directly. Python's `sqlite3.backup()` creates a consistent
snapshot; a stopped container copy is also consistent. Protect backup files at least as strongly as
the live data and test restoration periodically.

## SQLite restore drill

Choose the exact backup before running these commands. They replace the local host-run database, so
keep the application stopped until verification is complete.

```powershell
Copy-Item backups/where-is-my-money.db data/where-is-my-money.db -Force
if (Test-Path data/uploads) { Rename-Item data/uploads uploads.before-restore }
Expand-Archive backups/uploads.zip -DestinationPath data -Force
$env:DATABASE_URL = "sqlite:///data/where-is-my-money.db"
uv run alembic current
uv run alembic upgrade head
uv run python -c "import sqlite3; c=sqlite3.connect('data/where-is-my-money.db'); print(c.execute('PRAGMA integrity_check').fetchone()[0]); c.close()"
```

The integrity check must print `ok`. `alembic current` should show a known revision and
`alembic upgrade head` should succeed. Open `/health`, sign in, and spot-check one workspace before
removing `uploads.before-restore`.

For a Compose volume restore, stop `web`, preserve the current volume with `docker compose cp`, and
copy the previously verified `backups/container-data` tree back into the container:

```powershell
docker compose stop web
docker compose cp web:/app/data backups/container-data-before-restore
docker compose cp backups/container-data/. web:/app/data
docker compose start web
docker compose exec web alembic current
docker compose exec web alembic upgrade head
docker compose exec web python -c "import sqlite3; c=sqlite3.connect('/app/data/where-is-my-money.db'); print(c.execute('PRAGMA integrity_check').fetchone()[0]); c.close()"
```

## PostgreSQL migration guide

PostgreSQL is a future deployment option, not an automatically tested production mode yet. Use a
staging copy first.

1. Add the PostgreSQL driver with `uv add "psycopg[binary]>=3.2"` and commit the lockfile change.
2. Create an empty PostgreSQL database and a least-privilege application role.
3. Set a staging `DATABASE_URL`, for example
   `postgresql+psycopg://wimm_app:password@db.example.com/wimm`.
4. Run `uv run alembic upgrade head` against the empty database.
5. Export SQLite tables and import them in foreign-key order. Preserve integer primary keys and
   integer-cent money values; reset PostgreSQL sequences after loading explicit IDs.
6. Compare row counts per table and run the complete test suite against PostgreSQL before cutover.
7. Stop writes, take a final SQLite and upload backup, repeat the transfer, switch `DATABASE_URL`,
   and run a health and sign-in smoke test.
8. Replace the in-process auth rate limiter with a shared store before starting a second web
   process. Move retained uploads to private object storage before using ephemeral application
   hosts.

Never point the application at PostgreSQL and assume Alembic copied SQLite data: migrations create
the schema only. Keep the final SQLite backup until the PostgreSQL deployment has passed its restore
and rollback window.
