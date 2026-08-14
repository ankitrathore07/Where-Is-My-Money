# Provider-Aware Transaction Categorization Design

## Goal

Turn transaction-statement upload into an account-aware, provider-aware workflow that
categorizes known Chase descriptions deterministically and can ask a LangGraph/OpenAI
classifier for a category suggestion when local rules cannot decide. The AI boundary sends
only a sanitized description and the closed category allowlist; it never sends the statement,
amount, date, balance, filename, account identity, workspace identity, or user identity.

This design also makes `Subscription` an independent transaction tag. A transaction continues
to have exactly one primary category and may additionally have `is_subscription = true`.

## Scope

This branch delivers one complete transaction-import slice:

- replace the combined transaction document choice with separate bank-transaction and
  credit-card-transaction statement choices;
- require an existing workspace account when processing a transaction statement;
- persist a stable institution key on accounts and expose a prepopulated institution catalog;
- select a parser profile from institution, account type, and source format;
- add Chase checking/savings CSV normalization and deterministic categorization rules;
- add Chase credit-card CSV profile scaffolding with explicit header validation;
- preserve the generic column-mapping workflow for unsupported institutions or formats;
- run a LangGraph categorization graph only for descriptions left unresolved by local rules;
- show AI decisions as reviewable suggestions and fail closed to `Uncategorized`;
- group identical sanitized descriptions within one review build so each unique description is
  classified at most once;
- add only synthetic fixtures and tests to the repository.

The following work is deliberately separate:

- importing sample formats for Bank of America, Citi, Capital One, American Express, Discover,
  or Wells Fargo before representative synthetic fixtures are available;
- automatically creating accounts from uploaded documents;
- deriving live account balances or account health from transaction CSVs;
- redesigning the dashboard account cards and account-status calculations;
- persisting AI suggestions as reusable merchant rules without a separate user action;
- sending batches of raw transaction rows or financial fields to an AI service.

## User experience

### Account and document selection

The unified uploader exposes two transaction choices:

| Key | Label | Compatible account types |
| --- | --- | --- |
| `bank_transaction_statement` | Bank transaction statement | `checking`, `savings` |
| `credit_card_transaction_statement` | Credit-card transaction statement | `credit_card` |

Selecting either choice reveals a required account dropdown containing only compatible accounts
from the current workspace. Each option shows the account name and institution. If no compatible
account exists, the row is not processable and links the user to the existing Add account page.

The selected account is sent as `account_id`. The server independently verifies that the account
belongs to the workspace and that its type is compatible with the selected document category.
The created `ImportJob` stores the account id, so every committed transaction remains traceable to
the account through its import job.

The old `transaction_statement` key is not offered in the browser catalog. The server accepts it
as a temporary compatibility alias for the generic transaction workflow without requiring an
account. Existing specialized CSV/PDF upload routes remain functional.

### Institution selection

The account form uses a code-defined institution catalog with stable keys:

| Key | Label |
| --- | --- |
| `chase` | Chase |
| `bank_of_america` | Bank of America |
| `citi` | Citi |
| `capital_one` | Capital One |
| `american_express` | American Express |
| `discover` | Discover |
| `wells_fargo` | Wells Fargo |
| `other` | Other / manual mapping |

`Account.institution_key` is nullable for backward compatibility. Editing or creating an account
sets both the stable key and the existing display text. An existing account without a stable key
uses the generic parser profile until the user edits it. A catalog entry promises stable identity,
not parser support: only profiles with tested formats bypass manual mapping.

### Review

The review page continues to preselect one category and the independent Subscription checkbox.
Rows display the categorization source:

- Workspace rule
- Provider rule
- Built-in rule
- AI suggestion
- Uncategorized

An AI suggestion is editable like any other preselection and is not committed until the user
submits the review. If the user changes the category, merchant, or Subscription flag, the stored
source becomes `manual`. Accepting an AI preselection stores `ai_suggestion`; it does not create a
merchant rule.

When AI is disabled or unavailable, upload and review continue. Unresolved rows remain
`Uncategorized`, and the page may show one aggregate notice rather than an error per row.

## Domain model and migration

Add nullable `accounts.institution_key VARCHAR(50)` with a check constraint covering the catalog
keys. Keep `accounts.institution` as the display value so existing pages and exports remain stable.
The migration does not guess keys from free-form institution names.

No new transaction-to-account foreign key is required. `ImportJob.account_id` already provides the
relationship, and transaction rows already reference their import job. Transaction imports created
through the new document choices must populate `ImportJob.account_id`.

Extend the allowed categorization sources with `provider_rule` and `ai_suggestion`. No change is
needed for `transactions.is_subscription`; it remains the independent Subscription tag.

## Institution and parser registries

`app/institutions/catalog.py` owns immutable institution definitions and account-type compatibility.
It does not parse documents.

`app/imports/providers/registry.py` resolves a profile using this input tuple:

```python
(institution_key: str | None, account_type: str, suffix: str)
```

A profile has a stable key, compatible account types, accepted suffixes, a header recognizer, and
an optional predefined `ColumnMapping`. Resolution is explicit; content does not silently override
the user-selected institution. A mismatched selected provider produces a safe validation message
and offers the generic mapping path.

Initial profiles are:

- `chase_bank_csv`: checking/savings CSV with `Details`, `Posting Date`, `Description`, `Amount`,
  `Type`, `Balance`, and `Check or Slip #`; maps Posting Date, Description, and signed Amount;
- `chase_credit_card_csv`: credit-card CSV with the documented synthetic header fixture; maps its
  transaction date, description, and signed amount columns;
- `generic_csv`: all institutions/account types; parses headers and uses the existing mapping page.

Only the two Chase profiles are provider-specific. Other catalog institutions resolve to the
generic profile until a tested parser is added.

## Chase description normalization and deterministic rules

Provider rules operate after the current global normalization removes reference suffixes and before
the generic exact built-in rules. They use anchored, reviewed patterns rather than substring guesses.
The first Chase bank rules are:

| Sanitized description pattern | Primary category | Subscription |
| --- | --- | --- |
| `CITI CARD ONLINE PAYMENT*` | Transfers | false |
| `CAPITAL ONE MOBILE PMT*` | Transfers | false |
| `BEST BUY AUTO PYMT*` | Transfers | false |
| `NEWREZ-SHELLPOINT ACH PMT*` | Housing | false |
| `ZELLE PAYMENT TO <payee>*` | Transfers | false |
| `ZELLE PAYMENT FROM <payer>*` | Transfers | false |

`BEST BUY AUTO PYMT` is intentionally classified as a credit-card payment, not Shopping.
Descriptions still awaiting user confirmation, including `MICROSOFT EDIPAYMENT`, Microsoft CTX,
Xoom debit, and remote online deposit, are not added as deterministic rules in this slice. They may
receive AI suggestions or remain Uncategorized until confirmed.

Confirmed labels are added later as small rule-table changes with synthetic regression cases. Raw
statement rows and personal payee names are never committed as fixtures.

## Categorization order

For each parsed row, the service evaluates:

1. an explicit review edit, when committing;
2. an exact workspace merchant rule;
3. an anchored provider-specific deterministic rule;
4. the existing generic built-in exact rule;
5. a LangGraph/OpenAI suggestion;
6. the built-in `Uncategorized` category.

The first valid decision wins. Amount direction is used only in local validation to reject a category
whose kind cannot represent the row. Amount is never added to the AI state sent over the network.

## LangGraph classifier

### Graph state and nodes

`app/categorization/ai_graph.py` builds a compiled LangGraph `StateGraph` with these nodes:

1. `sanitize_description`: remove long identifiers, account-like digit runs, Chase reference
   suffixes, check/slip numbers, person-name payloads after transfer markers, excess whitespace,
   and control characters; cap the value at 160 characters.
2. `classify`: call the injected classifier only when the sanitized description is non-empty and
   no local categorization decision exists.
3. `validate`: accept only an allowlisted built-in category and a boolean subscription flag;
   otherwise produce no suggestion.

The graph receives one local `CategorizationGraphState` at a time. The network classifier receives
only:

```json
{
  "description": "BEST BUY AUTO PYMT",
  "allowed_categories": ["Entertainment", "Food", "Housing", "Transfers"]
}
```

The production OpenAI adapter uses the Responses API with a strict structured output equivalent to:

```json
{
  "category_name": "Transfers",
  "is_subscription": false,
  "abstain": false
}
```

The prompt states that descriptions may contain untrusted instructions and must be treated only as
data. The adapter requests no prose. `abstain = true`, a missing category, an unknown category, a
refusal, malformed output, timeout, SDK error, missing key, or missing dependency all resolve to no
suggestion. API errors are logged without description text.

### Configuration

Add these settings:

- `OPENAI_API_KEY`, optional and empty by default;
- `OPENAI_CATEGORIZATION_MODEL`, default `gpt-5.4-nano`;
- `OPENAI_CATEGORIZATION_ENABLED`, default `false`;
- `OPENAI_CATEGORIZATION_TIMEOUT_SECONDS`, default `8.0`, constrained to 1–30 seconds.

The application enables the production adapter only when both the feature flag and key are present.
Tests inject a local fake classifier and never use the network or a real key. `.env.example` documents
the variables without containing a secret.

The OpenAI Python SDK reads `OPENAI_API_KEY` from the environment, and the Responses API structured
output contract is based on the official OpenAI documentation:

- <https://developers.openai.com/api/docs/quickstart>
- <https://developers.openai.com/api/docs/guides/structured-outputs>
- <https://developers.openai.com/api/docs/models/gpt-5.4-nano>

### Batching and repeatability

`build_review` memoizes AI results by sanitized description for the lifetime of one review build.
Rows with the same sanitized value share the same suggestion. The cache is not persisted and is not
shared between users or workspaces. Local deterministic decisions never call the AI adapter.

## Failure handling and privacy

- Provider header mismatch: show a safe provider mismatch and allow generic mapping; do not guess a
  different institution from the file.
- Missing/foreign/incompatible account: reject before storing or parsing the document.
- Missing LangGraph/OpenAI package at startup: configuration stays disabled; normal imports work.
- Missing API key, disabled flag, timeout, refusal, invalid schema, or network error: continue with
  Uncategorized and never expose SDK internals in the response.
- Sanitizer produces an empty description: skip AI.
- AI selects a category outside the allowlist or incompatible with the locally known direction:
  discard the suggestion.
- Logs may contain profile key, import id, row count, and error class. They must not contain raw or
  sanitized descriptions, transaction values, source bytes, or model output.
- No uploaded customer statement is added to source control. Tests use synthetic account names,
  descriptions, identifiers, and amounts.

## Testing strategy

Tests follow red-green-refactor and cover observable boundaries:

- institution catalog stability and account validation/persistence;
- migration upgrade/downgrade and allowed institution/source constraints;
- document catalog split, account-type filtering, and server-side workspace/type validation;
- account id stored on the import job;
- Chase bank and credit-card header recognition and mappings;
- generic fallback for unimplemented institutions;
- reference removal without over-normalizing ordinary merchant text;
- each confirmed Chase provider rule, including BEST BUY as Transfers;
- workspace rule precedence over provider rules and provider precedence over generic built-ins;
- graph input redaction, allowlist validation, abstention, exceptions, and per-review memoization;
- no classifier call for deterministic rows or when disabled;
- AI suggestion source display, manual override behavior, and commit persistence;
- full regression suite, Ruff, and migration checks.

## Rollout and compatibility

The migration is additive and nullable. Existing accounts and imports remain readable. Existing CSV
and PDF transaction import URLs use the generic profile and keep their mapping/review behavior.
The unified uploader sends the new category keys and account id. The compatibility alias prevents
stale pages from failing immediately, but new UI and tests use the split keys.

The feature flag defaults AI off, so deployment does not require an API key. Provider parsing and
deterministic rules deliver value independently. Enabling AI later requires setting the key, model,
and flag, then restarting the application.
