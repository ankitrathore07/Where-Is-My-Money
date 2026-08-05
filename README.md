# Where Is My Money?

Where Is My Money? is a personal-finance learning project. It will help people
import their spending, understand where their money goes, plan expenses, and
work toward savings goals.

This first pull request deliberately contains only the foundation: a small
Python web app, tests, code-quality checks, and Docker files. It does not yet
store financial data, connect to banks, or call an LLM.

## What you need

- Python 3.12. The project uses uv to find or install it.
- uv. It is already installed on this machine.
- Docker Desktop is optional for this PR. It is needed only to build and run
  the application inside a container.

## Run the application locally

From the project directory, run:

    uv sync --all-groups
    uv run fastapi dev app/main.py

Open http://127.0.0.1:8000 in a browser. The automatic API documentation is
available at http://127.0.0.1:8000/docs.

### What those commands mean

- uv sync creates .venv, a project-specific virtual environment, and installs
  exactly the versions recorded in uv.lock.
- uv run runs a command inside that virtual environment. You do not need to
  activate .venv manually.
- fastapi dev starts a development web server and reloads it after Python files
  change.

## Run the checks

    uv run ruff check .
    uv run ruff format --check .
    uv run pytest

Ruff finds common code problems and checks formatting. Pytest runs the automated
tests in tests/. The same three checks run in GitHub Actions for every pull
request.

## Docker, in plain language

Docker packages an application and its runtime into a container. A container
lets the app run in a predictable environment on another computer or cloud
server. Docker does not replace uv during normal local Python development;
uv is the faster learning loop.

After Docker Desktop is installed and running, build and run the container:

    docker compose up --build

Open http://127.0.0.1:8000. Stop it with Control+C, then clean up the stopped
container with:

    docker compose down

The Compose file creates a named app_data volume. The upcoming database PR will
use this persistent storage for local application data.

### Run the checks inside the container

The image installs both runtime and development dependencies, so the same
checks that run locally and in CI also run inside the container. With the
container stopped, run:

    docker compose run --rm web uv run ruff check .
    docker compose run --rm web uv run ruff format --check .
    docker compose run --rm web uv run pytest

`--rm` removes the one-off container after the command finishes.

## Project map

    app/main.py              FastAPI application and routes
    app/core/config.py       Application settings read from environment
    app/templates/           HTML pages rendered by Python
    app/static/              Browser CSS and JavaScript assets
    tests/                   Automated tests
    pyproject.toml           Project metadata and Python dependencies
    uv.lock                  Exact dependency versions
    .env.example             Template for local environment variables
    Dockerfile               Recipe for one application container
    compose.yaml             Local Docker configuration
    .github/workflows/ci.yml Automated checks for GitHub pull requests

## Next step

PR 2 introduces the SQLite database schema and migrations. Until then, the
application intentionally remembers no financial information.
