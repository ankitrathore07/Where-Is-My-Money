# Beginner learning path

Use this order to learn the project without reading every file at once.

1. **Run one request.** Follow the README quick start, open `/health`, then read `app/main.py` and
   `tests/test_app.py`. Learn how the application factory, route, and test fit together.
2. **Learn settings and browser security.** Read `app/core/config.py`, `app/core/middleware.py`,
   `app/core/security.py`, `tests/test_config.py`, and `tests/test_security.py`. Change no secrets in
   source control.
3. **Trace authorization.** Follow one workspace route through `app/auth/dependencies.py`,
   `app/workspaces/dependencies.py`, its service, and its route test. Notice that foreign resources
   return 404.
4. **Trace a database write.** Read `app/db/models.py`, `app/db/session.py`, and one small account or
   category service. Then inspect the matching Alembic revision and test.
5. **Trace an upload safely.** Start at `app/documents/routes.py`, then follow one processor into its
   store, parser/extractor, review state, confirmation, and cleanup tests. Use only synthetic
   fixtures.
6. **Study deterministic finance.** Read dashboard or planning types, pure calculations, service,
   presentation code, and route in that order. Money is stored as integer cents; calculations must
   explain their source period.
7. **Make a small change.** Add a failing test, implement the smallest behavior, run the focused
   test, then run Ruff and the complete suite from the README.

Good first contributions are documentation corrections, a clearer safe error message with a test,
or an additional synthetic edge case. Avoid using real statements, payslips, email addresses,
OAuth credentials, or session values in tests, screenshots, logs, or issues.
