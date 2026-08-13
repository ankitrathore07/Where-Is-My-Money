# Troubleshooting

## `uv` cannot create or use its cache

Point the cache at an ignored project directory for the current PowerShell session:

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD ".cache/uv"
uv sync --all-groups --locked
```

## Pytest cannot access the Windows temporary directory

Use a repository-local ignored base directory:

```powershell
New-Item -ItemType Directory -Force data | Out-Null
uv run pytest --basetemp=data/.pytest-local
```

## Sessions reset after every restart

The development placeholder deliberately becomes a new ephemeral secret on startup. Generate a
stable local `SECRET_KEY` with the command in `.env.example`, store it only in `.env`, and restart.

## Production fails during settings startup

- `SECRET_KEY` must be present, must not equal the documented development placeholder, and must be
  at least 32 characters.
- `APP_ENV` must be exactly `development`, `test`, or `production`.
- `TRUSTED_HOSTS` must be a JSON list of explicit hostnames. Add the host used by the browser or
  reverse proxy; `*` is rejected.

## A request returns `Invalid host header`

Add the hostname—not a URL and not a path—to `TRUSTED_HOSTS`. For example:

```text
TRUSTED_HOSTS=["money.example.com","localhost"]
```

## Google sign-in reports a configuration or redirect error

Confirm the client is a Google OAuth Web application and its authorized redirect URI exactly
matches `http://127.0.0.1:8000/auth/google/callback` for local development. `localhost` and
`127.0.0.1` are different OAuth redirect origins.

## Scanned documents fail but text PDFs work

Install Tesseract and its English language data, then make sure `tesseract --version` works from the
same shell that starts FastAPI. The Docker targets install Tesseract automatically.

## An upload is rejected

Check the category, filename extension, browser-reported MIME type, and configured byte limit. A
matching extension alone is not enough: PDF/image decoders and CSV parsers must also accept the
contents. Request references in error pages correlate with redacted server logs; do not paste a
financial document or secret into an issue.

## Alembic reports a revision problem

Set `DATABASE_URL` to the intended database, then run:

```powershell
uv run alembic current
uv run alembic heads
uv run alembic upgrade head
```

Do not use `alembic stamp` to conceal a failed migration. Restore the database or fix the migration
state first.
