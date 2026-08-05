# Where Is My Money — PR Breakdown

Keep each pull request focused, independently testable, and small enough to review comfortably. Merge in this order.

## PR 1 — Project foundation

- Initialize the `uv` Python 3.12 project, package metadata, linting, formatting, tests, `.gitignore`, starter README, and CI that runs formatting, linting, and tests for every PR.
- Add the FastAPI application factory, health route, Jinja base template, Dockerfile, and local Compose configuration.
- **Done when:** `uv run pytest`, linting, and a local health check pass.

## PR 2 — Database foundation

- Add SQLAlchemy configuration for SQLite, the `data/` volume, Alembic, database session management, and migration commands.
- Add initial migrations for users, workspaces, memberships, invitations, uploaded files, categories, import jobs, transactions, merchant rules, payslips, income records, budgets, goals, and insights.
- **Done when:** a clean database can be created and migrated entirely from source control.

## PR 3 — Google sign-in and workspaces

- Implement Google OAuth, secure sessions, sign-out, private workspace creation, household workspaces, and equal-access memberships.
- Add pending email invitations and route-level workspace authorization.
- **Done when:** authorization tests prove private and shared data are isolated correctly.

## PR 4 — CSV statements and transactions

- Add private CSV upload, optional raw-file retention, column mapping, normalization, duplicate checks, transaction review, and commit flow.
- Add transaction list/filter pages and built-in categories.
- **Done when:** a sample statement imports only after review and duplicate re-upload is safe.

## PR 5 — Categorization rules

- Add manual recategorization, custom workspace categories, merchant normalization, and save-for-future merchant rules.
- Add tests for categorization precedence and rule isolation by workspace.
- **Done when:** correcting a merchant can categorize matching future transactions without overriding manual choices.

## PR 6 — Payslip income imports

- Add PDF/image payslip upload, local text extraction, OCR fallback, editable extraction review, and confirmed income records.
- Add income summaries based on net and gross pay; avoid automatically duplicating a bank deposit as a transaction.
- **Done when:** text and scanned sample payslips require confirmation and produce correct income totals.

## PR 7 — Deterministic LangGraph insights

- Add import and insights graphs, spending/category/merchant trends, recurring-charge detection, unusual-spend detection, and explanation links.
- Persist insight snapshots and render the dashboard report.
- **Done when:** fixed fixture data yields repeatable, evidence-backed insight output.

## PR 8 — Budgets and savings goals

- Add editable budget suggestions and monthly remaining-spend views.
- Add savings goals with target amount, current savings, deadline/monthly contribution calculations, and on-track status.
- **Done when:** the UI and tests correctly calculate a travel-goal contribution or target date.

## PR 9 — Production readiness and learning documentation

- Add structured redacted logging, file validation, error pages, backup/restore instructions, security configuration, and a PostgreSQL migration guide.
- Expand README and `docs/` with environment setup, architecture diagrams, troubleshooting, and a beginner learning path.
- **Done when:** a new contributor can run, test, back up, and restore the app locally from documented steps.

## Deferred future PR — LLM advisor with restricted tools

- Keep the model optional and disabled by default. Add only server-side, read-only tools for scoped spending summaries, category comparisons, recurring expenses, budget status, goal projections, and bounded transaction search.
- Derive the user and workspace from the authenticated session; never accept them as model-controlled parameters. Do not provide raw-file, database, shell, arbitrary-network, write, membership, or money-movement tools.
- Add consent controls, redacted audit logs, tool-result limits, and adversarial tests for prompt injection, cross-workspace access, unregistered tools, and write-action attempts.
- **Done when:** the advisor can explain approved aggregate data while every disallowed capability is rejected by automated tests.

## Deferred future PR — Bank and credit-card API provider

- Select the provider only after its supported institutions, region, pricing, and consent requirements are known.
- Implement the `BankConnector` adapter, linking flow, encrypted tokens, incremental sync, and reconciliation through the existing import pipeline.
- **Done when:** a linked account syncs safely without duplicating CSV-imported transactions.
