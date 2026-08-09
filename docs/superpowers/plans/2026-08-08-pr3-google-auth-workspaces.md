# PR 3 Google Sign-in and Workspaces Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add verified Google sign-in, secure browser sessions and sign-out, personal and equal-access household workspaces, email invitations, and a reusable route-level workspace authorization boundary.

**Architecture:** Keep the modular monolith and split PR3 into focused `auth`, `workspaces`, and `core.security` units. Authlib handles Google OpenID Connect, Starlette signs the HTTP-only session cookie, service functions own database invariants, and one FastAPI dependency derives every authorized workspace from the authenticated membership rather than trusting a raw workspace ID.

**Tech Stack:** Python 3.12, FastAPI/Starlette, Authlib, itsdangerous, SQLAlchemy 2, Jinja2, HTTPX, Pytest, Ruff, Alembic, SQLite.

## Global Constraints

- Implement only PR3 from `README.md` and `docs/where-is-my-money-pr-breakdown.md`; do not add PR4 or PR5 workflows.
- Accept only Google identities with `sub`, `email`, and `email_verified=true`.
- Keep all sessions HTTP-only, `SameSite=Lax`, signed with `SECRET_KEY`, and `Secure` outside development.
- Require signed double-submit CSRF on every state-changing form; OAuth callback state remains Authlib-managed.
- Derive workspace access from an authenticated membership and return 404 for both missing and unauthorized workspace IDs.
- Personal workspaces are private; every accepted household member has equal access to data, invitations, and settings.
- Store only a SHA-256 digest of an invitation bearer token and accept it only for the matching verified email before expiry.
- Log no OAuth tokens, invitation bearer tokens, client secrets, session secrets, or financial values.
- Use invented users, workspaces, and records in all fixtures.
- Run the same lint, format, migration, and test sequence as CI before completion.

## File structure

- `app/core/config.py`: validate runtime secrets and expose auth/session settings.
- `app/core/security.py`: signed CSRF tokens, invitation token hashing, and the in-process auth rate limiter.
- `app/auth/oauth.py`: construct the registered Google OAuth client.
- `app/auth/service.py`: validate Google claims and provision/update a user atomically.
- `app/auth/dependencies.py`: resolve optional/required current users from the signed session.
- `app/auth/routes.py`: sign-in, callback, and sign-out HTTP flow.
- `app/workspaces/service.py`: workspace, membership, and invitation business rules.
- `app/workspaces/dependencies.py`: route-level membership authorization.
- `app/workspaces/routes.py`: workspace list/detail/create/invite/accept HTTP flow.
- `app/main.py`: application factory and middleware/router assembly.
- `app/templates/*.html` and `app/static/styles.css`: minimal server-rendered PR3 UI only.
- `tests/test_config.py`, `tests/test_security.py`, `tests/test_auth.py`, `tests/test_workspaces.py`, and `tests/test_auth_routes.py`: focused unit and integration coverage.

---

### Task 1: Auth configuration and dependencies

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `app/core/config.py`
- Create: `tests/test_config.py`

**Interfaces:**
- Produces: `Settings(_env_file=None, app_env=..., secret_key=...)`, `Settings.is_production: bool`, and `Settings.session_https_only: bool`.
- Consumed by: application middleware, OAuth registration, CSRF, and tests in later tasks.

- [ ] **Step 1: Write failing configuration tests**

```python
def test_development_generates_secret_when_missing() -> None:
    configured = Settings(_env_file=None, app_env="development", secret_key=None)
    assert len(configured.secret_key) >= 32
    assert configured.session_https_only is False


def test_production_requires_explicit_secret() -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(_env_file=None, app_env="production", secret_key=None)


def test_production_enables_secure_cookies() -> None:
    configured = Settings(_env_file=None, app_env="production", secret_key="s" * 48)
    assert configured.is_production is True
    assert configured.session_https_only is True
```

- [ ] **Step 2: Run the tests and confirm the expected red state**

Run: `uv run pytest tests/test_config.py -v`

Expected: FAIL because `Settings` still has a static development secret and no cookie properties or production validator.

- [ ] **Step 3: Implement the minimal settings behavior**

Use a Pydantic `model_validator(mode="after")` to generate `secrets.token_urlsafe(48)` with a warning in development and raise `ValueError("SECRET_KEY is required in production")` otherwise. Add read-only `is_production` and `session_https_only` properties. Keep Google credentials empty by default so non-auth health checks remain usable.

- [ ] **Step 4: Add locked runtime dependencies**

Run: `uv add "authlib>=1.6.5" "itsdangerous>=2.2.0"`

Expected: `pyproject.toml` and `uv.lock` contain the two runtime dependencies without unrelated upgrades.

- [ ] **Step 5: Run focused tests and quality checks**

Run: `uv run pytest tests/test_config.py -v && uv run ruff check app/core/config.py tests/test_config.py && uv run ruff format --check app/core/config.py tests/test_config.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock app/core/config.py tests/test_config.py
git commit -m "feat: validate authentication settings"
```

### Task 2: CSRF, token hashing, and auth rate limiting

**Files:**
- Modify: `app/core/security.py`
- Create: `tests/test_security.py`

**Interfaces:**
- Produces: `create_csrf_token(secret_key: str) -> str`, `validate_csrf_token(secret_key: str, cookie_token: str | None, submitted_token: str | None, *, max_age: int = 3600) -> bool`, `hash_invitation_token(raw_token: str) -> str`, and `SlidingWindowRateLimiter(limit: int, window_seconds: float).allow(key: str, *, now: float | None = None) -> bool`.
- Consumed by: auth and workspace routes/services.

- [ ] **Step 1: Write failing security tests**

Cover these exact cases: a fresh CSRF token validates against itself; altered, missing, and wrong-secret tokens fail; invitation hashing is deterministic and does not equal the raw token; a limiter permits `limit` attempts, rejects the next, and permits another after the window.

```python
def test_rate_limiter_releases_attempts_after_window() -> None:
    limiter = SlidingWindowRateLimiter(limit=2, window_seconds=60)
    assert limiter.allow("client", now=0)
    assert limiter.allow("client", now=1)
    assert not limiter.allow("client", now=2)
    assert limiter.allow("client", now=61)
```

- [ ] **Step 2: Run the tests and confirm the expected red state**

Run: `uv run pytest tests/test_security.py -v`

Expected: collection FAIL because the new security interfaces do not exist.

- [ ] **Step 3: Implement the focused primitives**

Use `URLSafeTimedSerializer(secret_key, salt="where-is-my-money-csrf")`, a random nonce payload, `constant_time_compare`, `hashlib.sha256(raw_token.encode()).hexdigest()`, a `deque` per key, `time.monotonic()`, and a lock around limiter mutation. Do not add HTTP concerns to this file.

- [ ] **Step 4: Run focused tests and quality checks**

Run: `uv run pytest tests/test_security.py -v && uv run ruff check app/core/security.py tests/test_security.py && uv run ruff format --check app/core/security.py tests/test_security.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/core/security.py tests/test_security.py
git commit -m "feat: add auth security primitives"
```

### Task 3: Google identity service and personal workspace provisioning

**Files:**
- Create: `app/auth/__init__.py`
- Create: `app/auth/service.py`
- Create: `tests/test_auth.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Produces: `InvalidGoogleIdentity`, `GoogleIdentityConflict`, `normalize_email(email: str) -> str`, and `get_or_create_google_user(session: Session, claims: Mapping[str, object]) -> User`.
- Guarantees: a new user, one personal `Workspace`, and its `WorkspaceMembership` are flushed in the caller's transaction; repeat login updates name/email without creating duplicates.
- Consumed by: OAuth callback and workspace tests.

- [ ] **Step 1: Write failing auth service tests**

Write tests for: unverified email rejection; missing `sub` rejection; first login creating exactly one user/personal workspace/membership; repeat login updating display name without a second workspace; and a verified email already attached to another `sub` raising `GoogleIdentityConflict`.

```python
claims = {
    "sub": "google-sub-alex",
    "email": "Alex@example.test",
    "email_verified": True,
    "name": "Alex Example",
}
user = get_or_create_google_user(session, claims)
session.commit()
assert user.email == "alex@example.test"
assert len(user.owned_workspaces) == 1
assert user.owned_workspaces[0].is_personal
assert user.memberships[0].workspace_id == user.owned_workspaces[0].id
```

- [ ] **Step 2: Run tests and confirm red state**

Run: `uv run pytest tests/test_auth.py -v`

Expected: collection FAIL because `app.auth.service` does not exist.

- [ ] **Step 3: Implement minimal claim validation and provisioning**

Normalize emails with `strip().casefold()`. Treat `email_verified is True` as the only accepted verified value. Query by `google_sub`; never merge accounts by email. For a new user, add and flush the user, create `Workspace(name="Personal", is_personal=True, owner_id=user.id)`, flush it, then create `WorkspaceMembership(..., role="member")`. For an existing subject, refresh verified email/name only after the conflict query succeeds.

- [ ] **Step 4: Run focused and database regression tests**

Run: `uv run pytest tests/test_auth.py tests/test_db.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/auth tests/test_auth.py tests/conftest.py
git commit -m "feat: provision users from verified Google identities"
```

### Task 4: Household workspace, invitations, and authorization services

**Files:**
- Create: `app/workspaces/__init__.py`
- Create: `app/workspaces/service.py`
- Create: `tests/test_workspaces.py`

**Interfaces:**
- Produces: `WorkspaceRuleError`, `InvitationError`, `InvitationDispatch(invitation: WorkspaceInvitation, raw_token: str)`, `create_household_workspace(session: Session, user: User, name: str) -> Workspace`, `list_user_workspaces(session: Session, user_id: int) -> list[Workspace]`, `get_authorized_workspace(session: Session, user_id: int, workspace_id: int) -> Workspace | None`, `create_workspace_invitation(session: Session, workspace: Workspace, inviter: User, email: str, *, now: datetime | None = None) -> InvitationDispatch`, and `accept_workspace_invitation(session: Session, user: User, raw_token: str, *, now: datetime | None = None) -> Workspace`.
- Consumed by: workspace routes.

- [ ] **Step 1: Write failing workspace service tests**

Cover: creator membership; member-only listing; equal household access; personal cross-user isolation; unauthorized lookup returns `None`; personal invitation rejection; normalized pending invite; raw token differs from stored digest; duplicate live invite rejection; expiry rejection; email mismatch rejection; accepted membership creation; second acceptance rejection; and a user with an accepted membership can authorize the shared workspace but not either member's personal workspace.

- [ ] **Step 2: Run tests and confirm red state**

Run: `uv run pytest tests/test_workspaces.py -v`

Expected: collection FAIL because the workspace service does not exist.

- [ ] **Step 3: Implement workspace and invitation rules**

Trim workspace names and reject empty or longer-than-255 values. Always add a creator membership. Query authorized workspaces by joining `WorkspaceMembership`. Reject invites for `is_personal`, current members, and unexpired duplicate pending emails. Default expiry to `now + timedelta(days=7)`. Store `hash_invitation_token(raw_token)` and return the raw token only in `InvitationDispatch`. Acceptance looks up by hash, then checks `accepted`, expiry, and normalized verified email before adding membership and setting `accepted=True`.

- [ ] **Step 4: Run focused tests and regressions**

Run: `uv run pytest tests/test_workspaces.py tests/test_db.py tests/test_imports.py -v`

Expected: PASS and no cross-workspace fixture regression.

- [ ] **Step 5: Commit**

```bash
git add app/workspaces tests/test_workspaces.py
git commit -m "feat: add household workspace authorization"
```

### Task 5: Session, CSRF middleware, and Google auth routes

**Files:**
- Create: `app/auth/oauth.py`
- Create: `app/auth/dependencies.py`
- Create: `app/auth/routes.py`
- Create: `app/core/middleware.py`
- Modify: `app/main.py`
- Modify: `tests/test_app.py`
- Create: `tests/test_auth_routes.py`

**Interfaces:**
- Produces: `build_google_oauth(configured: Settings) -> OAuth`, `get_optional_current_user(...) -> User | None`, `require_current_user(...) -> User`, `CSRFMiddleware`, `require_csrf(request: Request) -> None`, `create_app(app_settings: Settings | None = None, *, google_oauth: object | None = None) -> FastAPI`, and exported `app = create_app()`.
- App state: `app.state.settings`, `app.state.google_oauth`, and `app.state.auth_rate_limiter`.
- Consumes: Tasks 1-4 interfaces.

- [ ] **Step 1: Write failing route tests with a fake Google client**

Build an in-memory SQLite `StaticPool`, override `get_db`, and inject a fake whose `google.authorize_redirect()` returns a provider redirect and whose `google.authorize_access_token()` returns `{"userinfo": verified_claims}`. Test: public home sets a CSRF cookie; missing CSRF rejects sign-in; valid sign-in redirects; callback creates a session and personal workspace; signed-in home identifies the user; sign-out rejects missing CSRF then clears the session; unverified callback fails safely; absent Google credentials return 503; production session `Set-Cookie` contains `HttpOnly`, `SameSite=lax`, and `Secure`; and rate-limit exhaustion returns 429.

- [ ] **Step 2: Run route tests and confirm red state**

Run: `uv run pytest tests/test_auth_routes.py -v`

Expected: collection FAIL because `create_app` and auth routes do not exist.

- [ ] **Step 3: Implement OAuth construction and current-user dependencies**

Register Google with `server_metadata_url="https://accounts.google.com/.well-known/openid-configuration"` and `client_kwargs={"scope": "openid email profile"}`. Resolve session `user_id` as an integer and return no user for malformed, missing, or deleted sessions. Required auth raises a 303 response to `/` before any workspace query.

- [ ] **Step 4: Implement CSRF middleware and dependency**

Set `request.state.csrf_token`; issue cookie `wimm_csrf` with `SameSite=Lax`, `Secure=configured.session_https_only`, path `/`, and a one-hour age. `require_csrf` accepts the form field `csrf_token` or `X-CSRF-Token` and uses Task 2 validation. Reject with 403 and a generic detail.

- [ ] **Step 5: Refactor application assembly and add auth routes**

The factory installs `SessionMiddleware` with cookie name `wimm_session`, max age seven days, `same_site="lax"`, `https_only=configured.session_https_only`, then `CSRFMiddleware`. `POST /auth/google` requires CSRF and rate limit before redirecting. The callback rate-limits, exchanges the code, passes only `token.get("userinfo", {})` to the auth service, commits, clears session state, saves `user_id`, and redirects to `/workspaces`. Sign-out requires CSRF/rate limit and clears the session. Map expected identity/configuration errors to safe 4xx/503 responses without provider data.

- [ ] **Step 6: Run auth/app tests**

Run: `uv run pytest tests/test_auth_routes.py tests/test_auth.py tests/test_app.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/auth app/core/middleware.py app/main.py tests/test_app.py tests/test_auth_routes.py
git commit -m "feat: add secure Google sign-in sessions"
```

### Task 6: Workspace routes and server-rendered UI

**Files:**
- Create: `app/workspaces/dependencies.py`
- Create: `app/workspaces/routes.py`
- Create: `app/templates/workspaces.html`
- Create: `app/templates/workspace_detail.html`
- Create: `app/templates/invitation.html`
- Modify: `app/templates/base.html`
- Modify: `app/templates/home.html`
- Modify: `app/static/styles.css`
- Modify: `app/main.py`
- Modify: `tests/test_auth_routes.py`

**Interfaces:**
- Produces HTTP dependency: `require_workspace(workspace_id: int, user: Annotated[User, Depends(require_current_user)], session: Annotated[Session, Depends(get_db)]) -> Workspace`.
- Routes: `GET /workspaces`, `POST /workspaces`, `GET /workspaces/{workspace_id}`, `POST /workspaces/{workspace_id}/invitations`, `GET /invitations/{token}`, and `POST /invitations/{token}/accept`.
- Consumes: authenticated user, CSRF dependency, `require_workspace`, and Task 4 services.

- [ ] **Step 1: Add failing workspace route tests**

Test with two synthetic users: signed-out workspace list redirects; each user sees only their personal workspace; one user gets 404 for the other's ID; household creation requires CSRF and returns a member-authorized detail page; a second accepted member can open that page; both members can invite; personal workspace invitation fails; stored token is a digest; mismatched and expired acceptances fail; valid acceptance redirects to the shared workspace; and a second acceptance fails. Assert no PR4/PR5 transaction or category workflow appears.

- [ ] **Step 2: Run the new route tests and confirm red state**

Run: `uv run pytest tests/test_auth_routes.py -k "workspace or invitation" -v`

Expected: FAIL with 404/route-not-found responses.

- [ ] **Step 3: Implement the HTTP authorization adapter**

`require_workspace` calls `get_authorized_workspace` only after `require_current_user`; raise `HTTPException(status_code=404, detail="Workspace not found")` for both missing and unauthorized IDs. Do not authorize via `owner_id`.

- [ ] **Step 4: Implement workspace and invitation routes**

Use fixed 303 redirects only. Catch `WorkspaceRuleError` and `InvitationError`, roll back, and return safe HTML errors. `GET /invitations/{token}` renders only generic sign-in/acceptance instructions; reveal workspace name only when the current verified email matches a live invitation. Both create routes and invitation acceptance use `require_csrf`. The invitation-create success page may display the one-time link because email delivery is outside PR3, but neither logs nor persists the raw token.

- [ ] **Step 5: Update templates and styles**

Add accessible labeled forms, visible error messages, membership lists, pending invitation status, and navigation/sign-out controls. Preserve the existing design language. Do not add JavaScript, financial forms, charts, imports, or categorization controls.

- [ ] **Step 6: Run route tests and full Pytest**

Run: `uv run pytest tests/test_auth_routes.py -v && uv run pytest`

Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add app/workspaces/dependencies.py app/workspaces/routes.py app/templates app/static/styles.css app/main.py tests/test_auth_routes.py
git commit -m "feat: add personal and household workspace flows"
```

### Task 7: Runtime and beginner documentation

**Files:**
- Modify: `.env.example`
- Modify: `compose.yaml`
- Modify: `README.md`
- Modify: `docs/where-is-my-money-pr-breakdown.md`

**Interfaces:**
- Documents the exact Google redirect URI `/auth/google/callback`, secret generation, cookie behavior, migrations, local checks, and PR3 completion boundary.
- Compose passes `APP_ENV`, `SECRET_KEY`, `DATABASE_URL`, `GOOGLE_CLIENT_ID`, and `GOOGLE_CLIENT_SECRET` without embedding real secrets.

- [ ] **Step 1: Update runtime configuration safely**

Use Compose `${NAME:-default}` substitutions. Keep development defaults usable and make comments state that production requires a real random `SECRET_KEY` and HTTPS. Never add `.env` or credentials.

- [ ] **Step 2: Update beginner-facing README**

Explain: creating a Google web OAuth client, adding `http://127.0.0.1:8000/auth/google/callback`, copying `.env.example` to `.env`, how OAuth differs from a password, how a signed session differs from authorization, why CSRF exists, and how CI repeats lint/format/migration/tests. Mark PR3 complete and state PR4 is CSV import.

- [ ] **Step 3: Update the PR breakdown completion note**

Keep PR3 scope text intact and add a concise status note only; do not rewrite future PR4/PR5 requirements.

- [ ] **Step 4: Run formatting and documentation checks**

Run: `uv run ruff check . && uv run ruff format --check . && git diff --check`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add .env.example compose.yaml README.md docs/where-is-my-money-pr-breakdown.md
git commit -m "docs: explain Google auth and workspace security"
```

### Task 8: Fresh migration, complete verification, and review

**Files:**
- Review all branch changes from `main...HEAD`.
- Modify only files needed to correct verified defects.

**Interfaces:**
- Produces a reviewable, migration-compatible PR3 branch with no PR4/PR5 scope.

- [ ] **Step 1: Run a fresh migration**

Create a task-specific empty SQLite database under the repository `data/` directory, set `DATABASE_URL` to its absolute SQLite URL, run `uv run alembic upgrade head`, inspect `uv run alembic current`, then remove only that exact disposable database after verifying its resolved path remains under this worktree.

Expected: revision `0005_accounts_balances (head)` and no schema drift requirement from PR3.

- [ ] **Step 2: Run the complete CI-equivalent suite**

Run: `uv sync --all-groups --locked`, `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pytest`.

Expected: all commands exit 0.

- [ ] **Step 3: Inspect the complete diff and privacy boundaries**

Run: `git status --short`, `git diff --check main...HEAD`, `git diff --stat main...HEAD`, and `git diff main...HEAD`.

Confirm: no credentials or real data; no raw invitation tokens in storage/logs; every mutation has CSRF; every workspace route uses membership authorization; both missing and foreign workspaces are 404; secure production cookies; no PR4/PR5 behavior; no unrelated refactor.

- [ ] **Step 4: Correct any review finding test-first**

For each finding, add or tighten a failing test, run it to observe failure, make the smallest correction, rerun the focused test, then rerun the complete suite.

- [ ] **Step 5: Commit final verified corrections if needed**

```bash
git add <only corrected files>
git commit -m "fix: tighten PR 3 security boundaries"
```

- [ ] **Step 6: Prepare delivery**

Push `codex/pr-3-google-auth-workspaces`, create a ready pull request targeting `main`, and include a beginner-friendly summary of OAuth, sessions, authorization, migrations, tests, and CI. Do not merge the PR.
