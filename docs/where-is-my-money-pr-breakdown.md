# Where Is My Money — PR Breakdown

Keep each pull request focused, independently testable, and small enough to review comfortably. Merge in this order.

## PR 1 — Project foundation

- Initialize the `uv` Python 3.12 project, package metadata, linting, formatting, tests, `.gitignore`, starter README, and CI that runs formatting, linting, and tests for every PR.
- Add the FastAPI application factory, health route, Jinja base template, Dockerfile, and local Compose configuration.
- **Done when:** `uv run pytest`, linting, and a local health check pass.

## PR 2 — Database foundation

The original single PR 2 created all ~13 tables at once, which is too large to review comfortably. It is split into four smaller PRs that each land the schema a feature actually needs. Merge in order.

### PR 2a — Database core, users, and workspaces

- Add SQLAlchemy configuration for SQLite, the `data/` volume, Alembic, database session management, and migration commands.
- Create the `app/db/` package (models, session, repositories) and expand `app/core/` beyond the `config.py` skeleton delivered in PR 1 (security helpers, logging setup).
- Add migrations for `users`, `workspaces`, `workspace_memberships`, and `workspace_invitations` — the tables PR 3 (auth) depends on.
- **Done when:** a clean database can be created from Alembic migrations and the user/workspace tables round-trip through the session.

### PR 2b — Imports and transactions

- Add migrations for `uploaded_files`, `import_jobs`, `transactions`, `categories`, and `merchant_rules` — the tables PR 4 (CSV import) and PR 5 (categorization) depend on.
- **Done when:** the import/transaction/category tables migrate cleanly and the workspace-level duplicate fingerprint constraint is enforced.

### PR 2c — Payslips and income

- Add migrations for `payslips` and `income_records` — the tables PR 6 depends on.
- **Done when:** the payslip/income tables migrate cleanly and income records stay separate from bank transactions.

### PR 2d — Planning and insights

- Add migrations for `budgets`, `savings_goals`, and `insight_snapshots` — the
  planning tables PR 8 depends on and the snapshot table reserved for the later
  PR 10 financial coach.
- **Done when:** all remaining tables migrate cleanly and a fresh database can be created and migrated entirely from source control (the original PR 2 exit criterion).

### PR 2e — Accounts and balance snapshots

- Add migrations for `accounts` (name, type, institution, is_liability, workspace) and `account_balance_snapshots` (account, balance cents, as-of date, source, optional file reference, workspace).
- Add an optional `account_id` column to `import_jobs` so an import can target a specific account.
- Index snapshots by (workspace_id, as_of_date) and (account_id, as_of_date).
- **Done when:** the account/balance tables migrate cleanly and a balance snapshot round-trips through the session linked to an account.

## PR 3 — Google sign-in and workspaces

- Implement Google OAuth, secure sessions, sign-out, private workspace creation, household workspaces, and equal-access memberships.
- Add pending email invitations and route-level workspace authorization.
- **Done when:** authorization tests prove private and shared data are isolated correctly.
- **Status:** Implemented with verified Google identities, signed/CSRF-protected browser sessions, hashed invitation tokens, and membership-derived route authorization.

## PR 4 — CSV statements and transactions

- Add private CSV upload, optional raw-file retention, column mapping, normalization, duplicate checks, transaction review, and commit flow.
- Add transaction list/filter pages and built-in categories.
- **Done when:** a sample statement imports only after review and duplicate re-upload is safe.
- **Status:** Implemented with private bounded uploads, explicit mapping and
  normalization, editable review-before-commit, workspace duplicate protection,
  optional source retention/cleanup retry, and workspace-scoped transaction
  filters. PR 5 categorization rules and manual recategorization remain separate.

## PR 5 — Categorization rules

- Add manual recategorization, custom workspace categories, merchant normalization, and save-for-future merchant rules.
- Add tests for categorization precedence and rule isolation by workspace.
- **Done when:** correcting a merchant can categorize matching future transactions without overriding manual choices.
- **Status:** Implemented with 21 built-in categories, deterministic exact-key
  rules, workspace-only custom categories, manual transaction corrections,
  independent Subscription labels, categorized CSV review, and acceptance tests
  for authorization, precedence, duplicate safety, and workspace isolation.

## PR 6 — Payslip income imports

- Add PDF/image payslip upload, local text extraction, OCR fallback, editable extraction review, and confirmed income records.
- Add income summaries based on net and gross pay; avoid automatically duplicating a bank deposit as a transaction.
- **Done when:** text and scanned sample payslips require confirmation and produce correct income totals.
- **Status:** Implemented with private bounded uploads, embedded PDF text
  extraction, local Tesseract OCR fallback for images and scanned PDFs, editable
  confirmation, pre-decode/render safety limits, atomic duplicate prevention,
  optional source cleanup, workspace-scoped gross/net summaries, and acceptance
  tests proving no bank transaction is created automatically.

## PR 7 — Centralized financial dashboard

- Add workspace-scoped account setup and manual balance entry using the existing
  account and balance-snapshot schema.
- Add a responsive dashboard for assets, liabilities, net worth, available cash,
  savings rate, account positions, and five-year net-worth and
  income-versus-spending trends.
- Use server-rendered Jinja, local Chart.js, and deterministic calculations; do
  not add LangGraph or an LLM.
- Add factual progress highlights, explicit partial-data states, and a synthetic
  demo path.
- **Done when:** fixed fixture data produces repeatable totals and charts, every
  value is workspace-scoped, and a new contributor can enter balances and view
  the polished dashboard.

## PR 8 — Budgets and savings goals

- Add editable budget suggestions and monthly remaining-spend views.
- Add savings goals with target amount, current savings, deadline/monthly contribution calculations, and on-track status.
- **Done when:** the UI and tests correctly calculate a travel-goal contribution or target date.

## PR 8b — Account statement balance imports

- Reuse PR 7 account management for checking, savings, credit card, 401(k),
  brokerage, mortgage, auto loan, student loan, and other accounts, including
  its manual-balance entry and net-worth dashboard.
- Implement statement processors for 401(k), brokerage/stocks (for example,
  Robinhood/Fidelity NetBenefits), mortgage, loan, and other account statements.
  Accept documented CSV/PDF/image formats, extract candidate account identity,
  balance, and as-of date locally, require editable confirmation, and save a
  workspace-scoped asset/liability snapshot only after confirmation.
- Keep categories unavailable until their processor and review workflow are
  implemented and tested.
- Refresh the PR 7 dashboard from confirmed snapshots without duplicating its
  net-worth calculations or manual-entry flow.
- Test workspace scoping, that totals do not change before confirmation, and
  that unsupported categories never claim support.
- **Done when:** synthetic examples produce confirmed snapshots, unsupported
  categories never claim support, and the existing dashboard shows correct
  totals.
- **Status:** Planned. PR 7 supplies accounts, manual balance entry, and the
  dashboard; processors and review workflows are not implemented yet.

## PR 9 — Production readiness and learning documentation

- Add structured redacted logging, file validation, error pages, backup/restore instructions, security configuration, and a PostgreSQL migration guide.
- Expand README and `docs/` with environment setup, architecture diagrams, troubleshooting, and a beginner learning path.
- **Done when:** a new contributor can run, test, back up, and restore the app locally from documented steps.

## PR 10 — LangGraph financial coach

- Add an optional conversational AI powered by LangGraph after accounts,
  dashboard reporting, budgets, and savings goals are available.
- Add server-side, workspace-scoped tools for spending questions, account
  positions, cash and net worth, category comparisons, recurring expenses,
  budget status, goal projections, and bounded transaction search.
- Help a member define a goal, calculate the needed monthly savings, and identify
  factual spending reductions that could close the gap.
- Allow the coach to draft a goal create/update action, but require a separate
  explicit human confirmation showing the exact fields before any write. Do not
  allow money movement, membership changes, raw-file access, database access,
  shell access, arbitrary network tools, or unregistered actions.
- Derive the user and workspace from the authenticated session; never accept
  either as model-controlled input. Add consent controls, redacted audit logs,
  bounded tool results, and adversarial tests for prompt injection and
  cross-workspace access.
- **Done when:** the coach answers fixture-backed financial questions, produces a
  correct goal plan, creates a goal only after confirmation, and rejects every
  disallowed capability.

## Deferred future PR — Bank and credit-card API provider

- Select the provider only after its supported institutions, region, pricing, and consent requirements are known.
- Implement the `BankConnector` adapter, linking flow, encrypted tokens, incremental sync, and reconciliation through the existing import pipeline.
- **Done when:** a linked account syncs safely without duplicating CSV-imported transactions.
