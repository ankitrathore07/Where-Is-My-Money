# PR 3 Google sign-in and workspaces design

## Goal and scope

PR 3 turns the existing user and workspace schema into the application's first
authenticated feature. It adds Google OpenID Connect sign-in, signed browser
sessions, sign-out, CSRF protection, personal and household workspaces,
equal-access household memberships, pending email invitations, and a reusable
route-level workspace authorization boundary.

This PR does not add statement import, transaction browsing, categorization,
payslip handling, insights, budgets, goals, account management, or net-worth
views. Those remain in PR 4 and later. All tests use invented users and records.

## Chosen approach

Use Authlib's Starlette/FastAPI OAuth client with Google's discovery document,
plus Starlette's signed cookie session middleware.

This approach fits the existing modular FastAPI application and delegates OAuth
state, authorization-code exchange, and OpenID Connect ID-token validation to a
maintained library. The session contains only the authenticated database user
ID and short-lived OAuth flow state. It is signed against tampering, HTTP-only,
`SameSite=Lax`, and marked `Secure` outside development.

Alternatives considered:

- A direct `httpx` OAuth implementation would reduce one dependency but would
  make this project responsible for discovery, state, token validation, key
  rotation, and subtle OpenID Connect failure modes.
- A hosted authentication platform would provide server-side session storage
  but add an external service and configuration that the project plan does not
  call for.

## Components and boundaries

### Configuration and application assembly

`app/core/config.py` owns authentication settings. Development may generate an
ephemeral signing secret with a prominent warning. Production refuses to start
without a non-default `SECRET_KEY`. Google client credentials remain optional
at process startup so health checks and tests can run, but the sign-in endpoint
returns a safe configuration error when they are absent.

`app/main.py` becomes an application factory. It assembles middleware, shared
templates, auth routes, workspace routes, and existing public routes while
preserving the exported `app` object used by FastAPI and CI.

### Authentication

`app/auth/` contains the Google client, current-user dependencies, and routes.

1. A CSRF-protected `POST /auth/google` begins sign-in and stores OAuth state in
   the signed session.
2. Google redirects to `GET /auth/google/callback`. Authlib validates state and
   the ID token. The app accepts only an identity with a stable `sub`, an email,
   and `email_verified=true`.
3. A user is found by Google subject. A verified email and display name may be
   refreshed. A verified email already attached to another Google subject is a
   conflict, never an account merge.
4. First sign-in atomically creates the user, a personal workspace, and the
   user's membership in that workspace.
5. The session is cleared and re-established with only `user_id` after login,
   which discards OAuth state and prevents session fixation.
6. `POST /auth/sign-out` requires CSRF, clears the whole session, and redirects
   to the public home page.

Authentication failures expose no tokens, client secrets, or provider response
bodies. The sign-in, callback, and sign-out endpoints use a small in-process
sliding-window limiter. This is appropriate for the project's documented
single-process SQLite deployment and is explicitly replaceable when the app
becomes multi-process.

### CSRF

A middleware maintains a signed, time-limited CSRF token in a `SameSite=Lax`
cookie and exposes the same token to server-rendered templates. Every
state-changing form sends it in a hidden field. Future HTMX requests may send it
as `X-CSRF-Token`. Validation requires the submitted token to match the cookie
in constant time and to have a valid signature and age.

The session cookie is HTTP-only because JavaScript never needs session access.
The CSRF cookie is readable so future HTMX code can copy it into a request
header; its value is signed and grants no authentication authority.

### Workspace service

`app/workspaces/service.py` owns database rules independently from HTTP:

- first sign-in provisions exactly one personal workspace and membership;
- users may create household workspaces, with the creator immediately becoming
  a member;
- every accepted household member has equal access to data, invitations, and
  settings, regardless of the legacy `role` column;
- personal workspaces cannot be shared or invited into;
- invitation email addresses are trimmed and case-normalized;
- a household may have at most one pending invitation per normalized email;
- invitation links use cryptographically random bearer tokens, stored as a
  one-way SHA-256 digest in the existing `token` column;
- invitations expire, may be accepted only once, and may be accepted only by an
  authenticated user whose verified email matches the invited email;
- acceptance and membership creation occur in one transaction.

The database schema already supports these behaviors, so no PR3 migration is
needed. Token hashing changes how new invitation rows use the existing column,
not its shape.

### Route authorization

`app/workspaces/dependencies.py` exposes one reusable dependency that receives
the authenticated user, path `workspace_id`, and database session. It loads a
workspace only through a matching membership. A missing workspace and a
workspace belonging to somebody else both return 404, which avoids confirming
that another user's private resource exists.

Every present and future workspace-scoped route must consume the authorized
workspace object from this dependency. Route code must not query by a raw
client-supplied workspace ID and must not infer membership from ownership alone.
Owners receive memberships during provisioning, so there is one authorization
rule for personal and household workspaces.

## User-facing routes

- `GET /` shows a Google sign-in form when signed out and the current user plus
  workspace navigation when signed in.
- `GET /workspaces` lists only the current user's memberships and shows pending
  invitations addressed to that user's verified email.
- `POST /workspaces` creates a household workspace.
- `GET /workspaces/{workspace_id}` is the first workspace-authorized page and
  shows members and pending invitations, but no PR4/PR5 financial workflows.
- `POST /workspaces/{workspace_id}/invitations` creates an invitation for a
  household workspace.
- `GET /invitations/{token}` shows an invitation-safe acceptance page without
  revealing household details to a mismatched or signed-out user.
- `POST /invitations/{token}/accept` validates CSRF, identity, token state, and
  expiry before adding membership.

Redirect targets are local, fixed application paths. User-controlled `next`
values are not accepted, avoiding open redirects.

## Error handling and privacy

- Unauthenticated protected routes redirect to the home page or return 401 for
  dependency-level API use; they never select workspace data first.
- Cross-workspace access is a 404.
- Invalid, expired, accepted, or email-mismatched invitations return a generic
  safe error and do not create membership.
- Duplicate household names are allowed because names are labels, not security
  identifiers.
- Database writes roll back together on validation or uniqueness failures.
- Logs contain route outcomes and internal identifiers when useful, but never
  OAuth tokens, invitation bearer tokens, secrets, or financial values.

## Test strategy

Implementation follows red-green-refactor in small slices:

1. Configuration tests cover generated development secrets and production
   fail-fast behavior.
2. Security tests cover signed CSRF, tampering, expiry, constant-time matching,
   and the rate limiter.
3. Auth service tests cover first and repeat login, verified-email enforcement,
   subject/email conflicts, and atomic personal workspace provisioning.
4. Workspace service tests cover creation, equal memberships, normalized pending
   invitations, hashed tokens, expiration, one-time acceptance, and email match.
5. Authorization tests create synthetic private and shared workspaces and prove
   members can enter only their own personal space and accepted shared spaces.
6. Route integration tests replace the Google client with a deterministic fake,
   exercise sign-in/callback/sign-out, verify secure cookie attributes, require
   CSRF for every mutation, and prove cross-workspace routes reveal no data.
7. Migration verification upgrades a fresh SQLite database to `head` and checks
   that the unchanged workspace schema remains compatible.

The full exit check is the repository's locked CI sequence: Ruff lint, Ruff
format check, Alembic upgrade on a fresh SQLite file, and the complete Pytest
suite.

## Beginner mental model

OAuth lets Google prove identity without giving this app a Google password. The
callback's `sub` is the durable identity; the email is verified contact data.
A signed session cookie is a small browser-held note that the server can detect
tampering with. CSRF protection is a second, signed value required on changes so
another website cannot silently submit the user's forms. Authorization happens
after authentication: sign-in answers “who are you?”, while the membership
query answers “may you use this workspace?”. CI repeats the same lint, format,
migration, and test commands on a clean machine so local assumptions cannot hide
breakage.
