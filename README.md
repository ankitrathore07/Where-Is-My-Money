# Where Is My Money?

Where Is My Money? is a privacy-conscious personal-finance learning project. It
now has a small Python web app, a complete database foundation, verified Google
sign-in, private personal workspaces, shared household workspaces, pending email
invitations, route-level workspace authorization, reviewed CSV imports,
deterministic transaction categorization, and reviewed local payslip imports with
gross/net income summaries.

The application does not yet connect to banks, calculate budgets, or call an
LLM. Those are separate later pull requests so each privacy boundary can be
reviewed and tested before financial workflows rely on it.

## What you need

- Python 3.12. The project uses uv to find or install it.
- uv. It manages Python, the virtual environment, and locked dependencies.
- A Google Cloud OAuth web client for interactive sign-in.
- The local Tesseract OCR executable only for image or scanned-PDF payslips.
  Text-based PDFs do not need it. The Docker image installs it automatically.
- Docker Desktop only if you want to run the container workflow.

## Configure Google sign-in

1. Open Google Cloud Console and create an OAuth client with application type
   **Web application**.
2. Add this exact authorized redirect URI:

       http://127.0.0.1:8000/auth/google/callback

3. Copy `.env.example` to `.env`.
4. Put the OAuth client ID and client secret in `GOOGLE_CLIENT_ID` and
   `GOOGLE_CLIENT_SECRET` inside `.env`.
5. Generate a session-signing secret:

       uv run python -c "import secrets; print(secrets.token_urlsafe(48))"

6. Put that output in `SECRET_KEY` inside `.env`. Never commit `.env`.

The Google client secret is used only on the server. It must never appear in a
template, browser response, log message, test fixture copied from a real
account, or Git commit.

## Run the application locally

From the project directory, run:

    uv sync --all-groups --locked
    uv run fastapi dev app/main.py

Open http://127.0.0.1:8000. The API documentation is at
http://127.0.0.1:8000/docs.

What those commands mean:

- `uv sync` creates `.venv`, a project-specific Python environment, and
  installs exactly the dependency versions recorded in `uv.lock`.
- `--all-groups` includes developer tools such as Pytest and Ruff.
- `--locked` refuses to silently change the lock file.
- `uv run` runs a command inside `.venv`, so you do not activate it manually.
- `fastapi dev` starts a local server and reloads after Python file changes.

If Google credentials are absent, the public home and health routes still run,
but the sign-in route returns a safe configuration error. In development, a
missing or documented placeholder `SECRET_KEY` becomes a random ephemeral key
with a warning. Restarting the process then signs everyone out. Production
instead fails fast unless a non-placeholder key of at least 32 characters is
provided.

## Auth and authorization in plain language

These four ideas solve different problems:

1. **Google OAuth / OpenID Connect proves identity.** The app sends the browser
   to Google and receives a validated identity response. The durable Google
   `sub` claim identifies the account; a verified email alone never merges two
   accounts. The app never receives the user's Google password.
2. **A signed session remembers identity.** After the callback, the browser gets
   an HTTP-only cookie containing only the local user ID. A server secret signs
   it, so changing the value invalidates it. Production also marks it `Secure`,
   which means browsers send it only over HTTPS.
3. **CSRF protects changes.** A second signed token must accompany sign-in,
   sign-out, workspace creation, invitations, and acceptance. This stops another
   website from silently submitting those forms through an already signed-in
   browser.
4. **Membership authorizes a workspace.** Authentication answers “who are
   you?” Authorization answers “may this user open this workspace?” Every
   workspace route loads data through a matching membership. Missing and foreign
   workspace IDs both return 404 so the app does not reveal private resources.

Each first-time user receives one personal workspace and its membership.
Personal workspaces cannot be invited into. A household creator and every
accepted invitee have the same member access to household data, invitations,
and settings. Invitation links expire after seven days, work once, require the
matching verified email, and are stored only as SHA-256 digests. Until an email
delivery service exists, the app shows the one-time link to the household member
who creates it so they can share it privately.

## Import a CSV statement

1. Sign in, open a personal or household workspace, and select **Import CSV**.
2. Choose a synthetic or personal UTF-8 CSV no larger than 5 MiB. Select whether
   the app should delete the raw file after a successful import or retain it
   privately.
3. Map the date and description columns. Choose either one signed amount column
   or separate debit and credit columns, then select the CSV's explicit date
   format.
4. Review every normalized row. You can correct its date, description, merchant,
   category, Subscription label, or amount and exclude any row before committing.
   Nothing is added to the transaction table before this step.
5. Commit the selected rows. Negative values mean money out and positive values
   mean money in. The transaction page can filter by dates, category, direction,
   Subscription, description, or normalized merchant.

The default retention choice deletes the raw CSV only after the database commit
succeeds. Selecting **retain** keeps it below the configured private local data
directory; retained files have no browser download page. Uploading the same
statement again resumes an unfinished import or safely reports an already
committed import without adding duplicate transactions.

The review page applies the same workspace and built-in rules used everywhere
else, but the visible reviewed decision is what commit saves. A rule change made
after preview cannot silently replace the decision that the member approved.

## Import a payslip and confirm income

1. Sign in, open a workspace, and select **Import payslip**.
2. Choose a PDF, PNG, or JPEG no larger than 10 MiB. Choose whether the raw
   source should be deleted after confirmation or retained privately.
3. The app first reads embedded PDF text. For an image or scanned PDF, it runs
   the Tesseract executable on this computer. It never sends the document to an
   OCR website or API.
4. Review and edit the employer, pay period, pay date, gross pay, net pay,
   taxes, and deductions. Extraction is only a suggestion: no income record
   exists yet.
5. Select **Confirm income record**. The app saves exactly the reviewed values
   and shows workspace totals for gross and net pay.

Income records are intentionally separate from bank transactions. If a CSV
statement also contains the paycheck deposit, confirming the payslip does not
create a second transaction or attempt to link the two rows.

### Install local OCR for scanned payslips

Run this first to see whether Tesseract is already available:

    tesseract --version

If the command is missing, install Tesseract 5 with English language data using
the instructions for your operating system in the
[official Tesseract installation guide](https://tesseract-ocr.github.io/tessdoc/Installation.html),
then make sure `tesseract` is on your `PATH`. Restart the terminal and run the
version command again. You can still import text-based PDFs while Tesseract is
not installed; scanned files display a local setup message instead of being
sent elsewhere.

## Categorization rules in plain language

Every transaction has one primary category and a separate **Subscription**
label. For example, Netflix can be **Entertainment** and Subscription at the
same time. Subscription means an app or service membership; it does not mean
every repeating bill. Rent, electricity, insurance, and similar recurring bills
keep their normal categories without the Subscription label. Later reporting
will detect recurrence from transaction cadence.

The built-in categories are Income, Transfers, Housing, Utilities, Groceries,
Dining & Drinks, Transportation, Shopping, Entertainment, Software & Online
Services, Health & Fitness, Insurance, Education, Travel, Personal Care, Pets,
Childcare,
Gifts & Donations, Taxes & Fees, Cash & ATM, and Uncategorized. Eating out,
cafes, bars, takeout, and restaurant delivery belong in **Dining & Drinks**.
Ambiguous payment processors such as generic PayPal, Venmo, Zelle, Square, or
Stripe descriptions safely remain **Uncategorized** instead of guessing.

Automatic categorization uses this order:

1. Keep an explicit manual choice on an existing transaction.
2. Apply an exact merchant rule saved in the active workspace.
3. Apply a matching built-in merchant rule with the correct money direction.
4. Fall back to the built-in **Uncategorized** category.

To create a category, open a workspace, select **Categories**, enter a name, and
choose expense, income, or transfer. Custom categories belong to that workspace:
all accepted household members can use them, while another workspace cannot see
or select them.

Select **Edit** beside a transaction to correct its friendly merchant, category,
or Subscription label. Saving normally changes only that transaction and records
the source as manual. Select **Use for matching future transactions** to also
save a workspace rule. That rule applies only to later transactions whose
canonical merchant key exactly matches the original statement description. It
does not use wildcards, fuzzy matching, or prefixes, and it does not rewrite old
transactions. Import-review corrections are manual decisions but do not create
future rules; use the transaction edit page when you want that behavior.

## Run the checks

    uv run ruff check .
    uv run ruff format --check .
    uv run pytest

- Ruff lint catches common Python errors and risky patterns.
- Ruff format checks that contributors produce the same layout.
- Pytest runs synthetic unit and integration fixtures. No real financial or
  identity data belongs in tests. CSV coverage uses only the included fictional
  `synthetic_checking.csv` fixture, and payslip coverage uses only the fictional
  Northstar Bicycle Works fixture.
- Alembic migrations describe database changes in source control. PR 4 adds a
  reversible data migration for the built-in categories, PR 6 adds the unique
  one-income-record-per-payslip rule, and CI upgrades a fresh SQLite database
  through every revision.

GitHub Actions is the continuous integration (CI) system. On every push to
`main` and every pull request, a clean Linux runner installs Python 3.12 and the
locked dependencies, runs lint and formatting, migrates a fresh database, and
runs all tests. Passing locally is the quick feedback loop; passing CI proves the
work did not depend on an uncommitted file or one developer's machine.

## Database transactions in plain language

SQLAlchemy's `Session` groups reads and writes. A successful Google callback
creates the user, personal workspace, and membership in one transaction. A
successful invitation acceptance marks the invitation used and creates the
membership in one transaction. `commit()` saves the whole group; `rollback()`
discards it after a validation or provider error. That prevents half-created
security state.

## Docker

Docker packages the app and Python runtime into a repeatable container. After
Docker Desktop is running:

    docker compose up --build

The image includes Tesseract and its English data locally, so scanned payslips
work inside Docker without installing a separate host executable.

Compose reads the same `APP_ENV`, `SECRET_KEY`, `DATABASE_URL`,
`GOOGLE_CLIENT_ID`, and `GOOGLE_CLIENT_SECRET` values from `.env`, and mounts a
named `app_data` volume for SQLite persistence. Open http://127.0.0.1:8000. Stop
with Control+C, then remove the stopped container with:

    docker compose down

To run CI-equivalent checks inside one-off containers:

    docker compose run --rm web uv run ruff check .
    docker compose run --rm web uv run ruff format --check .
    docker compose run --rm web uv run pytest

`--rm` removes only the one-off test container. It does not delete the named
database volume.

## Project map

    app/main.py                 FastAPI factory, public routes, middleware assembly
    app/auth/                   Google OAuth, identity service, session dependencies
    app/workspaces/             Membership, invitation, and authorized routes
    app/imports/                CSV mapping, review, categorization, duplicates, storage
    app/payslips/               PDF/image storage, local extraction/OCR, review, income
    app/categorization/         Merchant normalization, built-in catalog, precedence
    app/categories/             Workspace category validation and routes
    app/transactions/           Scoped queries, manual edits, and future merchant rules
    app/core/config.py          Environment settings and production validation
    app/core/security.py        CSRF, invitation hashing, and auth rate limiting
    app/core/middleware.py      Browser CSRF cookie and form enforcement
    app/db/models.py            SQLAlchemy tables and relationships
    app/db/session.py           Engine and transaction/session dependency
    app/templates/              Server-rendered HTML pages
    app/static/                 Browser CSS assets
    migrations/                 Ordered Alembic database schema changes
    tests/                      Synthetic automated tests
    pyproject.toml              Project metadata and direct dependencies
    uv.lock                     Exact transitive dependency versions
    compose.yaml                Local container configuration
    .github/workflows/ci.yml    Clean-machine quality checks
    docs/                       Product plan, PR scope, designs, and implementation plans

## Next step

PR 7 adds a centralized, responsive financial dashboard plus basic account setup
and manual balance entry. It uses deterministic Python calculations and locally
served charts, not AI.

LangGraph is reserved for PR 10's optional financial coach, after accounts,
budgets, and savings goals exist. That later assistant will answer scoped money
questions and help draft goal plans, while explicit confirmation remains
required before it can create or change a goal. It will never move money.
