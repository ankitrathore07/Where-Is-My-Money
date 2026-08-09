# PR5 Built-In Categorization and Subscription Rules Design

**Status:** Proposed expansion of PR5 for review.

**Relationship to PR5:** This document extends the approved PR5 categorization design. It defines
the built-in spending taxonomy, initial merchant catalog, subscription label, ambiguity rules, and
catalog-maintenance policy. It does not create a separate product PR.

## Goal

Give a new workspace a useful answer to “where is my money going?” before the member creates any
custom rules, while avoiding guesses that are difficult to notice or correct.

The built-in catalog is a safe starting point, not an exhaustive merchant database. A workspace
member can correct any result and save an exact workspace rule for future transactions.

## Two independent dimensions

Each transaction has one primary category and an independent subscription label.

Examples:

| Transaction | Primary category | Subscription |
| --- | --- | --- |
| Netflix monthly charge | Entertainment | Yes |
| Movie theater ticket | Entertainment | No |
| Spotify monthly charge | Entertainment | Yes |
| Restaurant dinner | Dining & Drinks | No |
| DashPass membership | Dining & Drinks | Yes |
| Microsoft 365 | Software & Online Services | Yes |
| One-time software purchase | Software & Online Services | No |
| Planet Fitness membership | Health & Fitness | Yes |

`Subscription` must not replace the primary category. This lets reports show both total
entertainment spending and total recurring subscription spending without duplicating a
transaction.

## Categorization precedence

The complete precedence remains:

1. Manual transaction category and subscription choice.
2. Exact merchant rule saved in the active workspace.
3. Exact built-in merchant rule.
4. Built-in `Uncategorized` with subscription set to `No`.

Manual choices are never automatically overwritten. Workspace rules never cross workspace
boundaries. A later built-in catalog update cannot change transactions that were already reviewed
and committed.

## Built-in primary categories

PR4 must seed these stable built-in rows before PR5 integration:

| Category name | Kind |
| --- | --- |
| Income | income |
| Transfers | transfer |
| Housing | expense |
| Utilities | expense |
| Groceries | expense |
| Dining & Drinks | expense |
| Transportation | expense |
| Shopping | expense |
| Entertainment | expense |
| Software & Online Services | expense |
| Health & Fitness | expense |
| Insurance | expense |
| Education | expense |
| Travel | expense |
| Personal Care | expense |
| Pets | expense |
| Childcare | expense |
| Gifts & Donations | expense |
| Taxes & Fees | expense |
| Cash & ATM | expense |
| Uncategorized | expense |

Names are stable application identifiers in V1. Custom workspace categories may supplement them
without changing the system catalog.

### Income

Money received as earnings or income.

Includes payroll, salary, wages, interest income, and other clearly identified income. Refunds are
not automatically classified as Income; they should retain the original spending category when it
can be determined.

### Transfers

Money moved between accounts or people when it is clearly a transfer rather than spending.

Includes explicit internal account transfers, credit-card payments, and savings transfers. Generic
PayPal, Venmo, Zelle, and Cash App descriptions are not automatically categorized because they may
represent purchases, reimbursements, gifts, rent, or transfers.

### Housing

Includes rent, mortgage payments, homeowners or condominium association fees, and property
management charges. Home repairs and furnishings are not Housing by default; they use Shopping or
remain Uncategorized unless the merchant is specific enough.

### Utilities

Includes electricity, natural gas, water, sewer, trash, home internet, mobile phone, and similar
household services.

### Groceries

Includes supermarkets, grocery stores, and food purchased for preparation at home. Meal-kit
deliveries use Groceries; a recurring meal-kit plan also receives the Subscription label.

### Dining & Drinks

Includes restaurants, fast food, cafés, coffee shops, bars, takeout, restaurant delivery orders,
and tips included in those charges.

It does not include supermarkets or ordinary meal-kit food. A restaurant-delivery membership such
as DashPass uses Dining & Drinks plus Subscription.

### Transportation

Includes fuel, rideshare trips, public transit, parking, tolls, vehicle maintenance, and car washes.
Vehicle purchases, loan principal, and insurance use their own applicable categories.

### Shopping

Includes general retail, clothing, electronics, home goods, and other personal purchases that do
not have a more specific category.

Large mixed retailers are intentionally broad defaults. For example, a Target purchase defaults to
Shopping even though a member may correct a specific grocery purchase and save a workspace rule if
that better matches their habits.

### Entertainment

Includes video and music streaming, movie theaters, games, live entertainment, and recreational
media. Subscription-specific services also receive the Subscription label.

### Software & Online Services

Includes productivity software, cloud storage, password managers, VPN services, developer tools,
and other digital services that are not primarily entertainment. Subscription-specific products
also receive the Subscription label.

### Health & Fitness

Includes doctors, dentists, pharmacies, vision care, therapy, gyms, and fitness memberships. A gym
or fitness membership also receives the Subscription label when the merchant key is
subscription-specific.

### Insurance

Includes auto, home, renters, life, and health-insurance premiums when the merchant is clearly an
insurer.

### Education

Includes tuition, books, courses, learning platforms, and education-specific services. A recurring
learning platform receives the Subscription label only when its product is normally sold as a
subscription.

### Travel

Includes airlines, hotels, vacation rentals, rental cars, and travel booking services. Local
rideshare and transit remain Transportation.

### Personal Care

Includes salons, barbers, spas, cosmetics, and personal-care services when the merchant identity is
specific enough.

### Pets

Includes pet stores, veterinary care, grooming, and pet-specific services. An ordinary purchase at
a pet store is not marked as a subscription merely because the merchant offers autoship.

### Childcare

Includes daycare, babysitting, and childcare-specific services. Generic peer-to-peer payments are
not assumed to be childcare.

### Gifts & Donations

Includes merchants that are clearly charitable organizations and manual gift classifications.
Ordinary retail purchases are not automatically treated as gifts.

### Taxes & Fees

Includes clearly identified tax payments, bank fees, account fees, late fees, and service charges.

### Cash & ATM

Includes cash withdrawals from an ATM. Subsequent cash spending cannot be inferred from the bank
transaction and remains represented by the withdrawal unless the member records it separately.

### Uncategorized

The safe fallback for an unknown or ambiguous description. Uncategorized is an explicit review
state, not an error.

## Built-in merchant-rule shape

Each built-in rule contains:

```python
@dataclass(frozen=True)
class BuiltinMerchantRule:
    merchant_keys: tuple[str, ...]
    normalized_merchant: str
    category_name: str
    is_subscription: bool = False
    amount_direction: Literal["expense", "income", "either"] = "expense"
```

`merchant_keys` contains exact canonical keys produced by PR5's existing NFKC/case/punctuation
normalizer. A rule never accepts a regular expression, user-authored wildcard, or fuzzy score.

`amount_direction` prevents an income descriptor from categorizing an outgoing charge as Income.
Workspace rules normally use `either` so a merchant refund keeps the same category as the original
purchase.

## Initial built-in merchant catalog

These entries define the intended V1 baseline. Keys are examples of canonical bank descriptions;
additional exact aliases may be added from anonymized fixtures when they map to the same merchant
without increasing ambiguity.

### Income and transfers

| Exact merchant key | Display merchant | Category | Subscription | Direction |
| --- | --- | --- | --- | --- |
| PAYROLL | Payroll | Income | No | Income |
| DIRECT DEPOSIT PAYROLL | Payroll | Income | No | Income |
| INTEREST PAYMENT | Interest | Income | No | Income |
| INTERNAL TRANSFER | Internal Transfer | Transfers | No | Either |
| ONLINE TRANSFER | Account Transfer | Transfers | No | Either |
| CREDIT CARD PAYMENT | Credit Card Payment | Transfers | No | Expense |

### Housing and utilities

| Exact merchant key | Display merchant | Category | Subscription | Direction |
| --- | --- | --- | --- | --- |
| RENT PAYMENT | Rent | Housing | No | Expense |
| MORTGAGE PAYMENT | Mortgage | Housing | No | Expense |
| HOA PAYMENT | HOA | Housing | No | Expense |
| COMED | ComEd | Utilities | No | Expense |
| XFINITY | Xfinity | Utilities | No | Expense |
| AT T WIRELESS | AT&T | Utilities | No | Expense |
| VERIZON WIRELESS | Verizon | Utilities | No | Expense |
| T MOBILE | T-Mobile | Utilities | No | Expense |

Utility names vary greatly by region. New providers should be added only from fixture-backed exact
keys.

### Groceries

| Exact merchant key | Display merchant | Category | Subscription | Direction |
| --- | --- | --- | --- | --- |
| WHOLE FOODS MARKET | Whole Foods | Groceries | No | Expense |
| TRADER JOE S | Trader Joe's | Groceries | No | Expense |
| ALDI | Aldi | Groceries | No | Expense |
| KROGER | Kroger | Groceries | No | Expense |
| SAFEWAY | Safeway | Groceries | No | Expense |
| PUBLIX | Publix | Groceries | No | Expense |
| H E B | H-E-B | Groceries | No | Expense |
| WEGMANS | Wegmans | Groceries | No | Expense |
| INSTACART | Instacart | Groceries | No | Expense |
| INSTACART PLUS | Instacart+ | Groceries | Yes | Expense |
| HELLOFRESH | HelloFresh | Groceries | Yes | Expense |

### Dining & Drinks

| Exact merchant key | Display merchant | Category | Subscription | Direction |
| --- | --- | --- | --- | --- |
| MCDONALD S | McDonald's | Dining & Drinks | No | Expense |
| STARBUCKS | Starbucks | Dining & Drinks | No | Expense |
| CHIPOTLE | Chipotle | Dining & Drinks | No | Expense |
| CHICK FIL A | Chick-fil-A | Dining & Drinks | No | Expense |
| PANERA BREAD | Panera Bread | Dining & Drinks | No | Expense |
| DOMINO S | Domino's | Dining & Drinks | No | Expense |
| DOORDASH | DoorDash | Dining & Drinks | No | Expense |
| DOORDASH DASHPASS | DashPass | Dining & Drinks | Yes | Expense |
| UBER EATS | Uber Eats | Dining & Drinks | No | Expense |
| UBER ONE | Uber One | Dining & Drinks | Yes | Expense |
| GRUBHUB | Grubhub | Dining & Drinks | No | Expense |

### Transportation

| Exact merchant key | Display merchant | Category | Subscription | Direction |
| --- | --- | --- | --- | --- |
| UBER TRIP | Uber | Transportation | No | Expense |
| LYFT RIDE | Lyft | Transportation | No | Expense |
| SHELL | Shell | Transportation | No | Expense |
| EXXON | Exxon | Transportation | No | Expense |
| CHEVRON | Chevron | Transportation | No | Expense |
| BP | BP | Transportation | No | Expense |
| CTA VENTRA | CTA | Transportation | No | Expense |
| PARKING | Parking | Transportation | No | Expense |
| TOLL PAYMENT | Tolls | Transportation | No | Expense |

### Shopping

| Exact merchant key | Display merchant | Category | Subscription | Direction |
| --- | --- | --- | --- | --- |
| AMAZON | Amazon | Shopping | No | Expense |
| TARGET | Target | Shopping | No | Expense |
| WALMART | Walmart | Shopping | No | Expense |
| COSTCO | Costco | Shopping | No | Expense |
| AMAZON PRIME | Amazon Prime | Shopping | Yes | Expense |
| BEST BUY | Best Buy | Shopping | No | Expense |
| HOME DEPOT | Home Depot | Shopping | No | Expense |
| LOWE S | Lowe's | Shopping | No | Expense |
| MACY S | Macy's | Shopping | No | Expense |
| IKEA | IKEA | Shopping | No | Expense |

### Entertainment subscriptions and purchases

| Exact merchant key | Display merchant | Category | Subscription | Direction |
| --- | --- | --- | --- | --- |
| NETFLIX COM | Netflix | Entertainment | Yes | Expense |
| SPOTIFY USA | Spotify | Entertainment | Yes | Expense |
| HULU | Hulu | Entertainment | Yes | Expense |
| DISNEY PLUS | Disney+ | Entertainment | Yes | Expense |
| MAX COM | Max | Entertainment | Yes | Expense |
| PARAMOUNT PLUS | Paramount+ | Entertainment | Yes | Expense |
| PEACOCK TV | Peacock | Entertainment | Yes | Expense |
| YOUTUBE PREMIUM | YouTube Premium | Entertainment | Yes | Expense |
| YOUTUBE TV | YouTube TV | Entertainment | Yes | Expense |
| APPLE MUSIC | Apple Music | Entertainment | Yes | Expense |
| AUDIBLE | Audible | Entertainment | Yes | Expense |
| KINDLE UNLIMITED | Kindle Unlimited | Entertainment | Yes | Expense |
| AMC THEATRES | AMC Theatres | Entertainment | No | Expense |
| STEAM GAMES | Steam | Entertainment | No | Expense |

### Software & Online Services

| Exact merchant key | Display merchant | Category | Subscription | Direction |
| --- | --- | --- | --- | --- |
| MICROSOFT 365 | Microsoft 365 | Software & Online Services | Yes | Expense |
| ADOBE CREATIVE CLOUD | Adobe Creative Cloud | Software & Online Services | Yes | Expense |
| DROPBOX | Dropbox | Software & Online Services | Yes | Expense |
| GOOGLE ONE | Google One | Software & Online Services | Yes | Expense |
| ICLOUD PLUS | iCloud+ | Software & Online Services | Yes | Expense |
| ONEPASSWORD | 1Password | Software & Online Services | Yes | Expense |
| NORDVPN | NordVPN | Software & Online Services | Yes | Expense |
| GITHUB COPILOT | GitHub Copilot | Software & Online Services | Yes | Expense |

Generic `APPLE COM BILL`, `GOOGLE`, and `MICROSOFT` keys are deliberately excluded because they may
represent apps, devices, games, cloud services, or one-time purchases.

### Health & Fitness

| Exact merchant key | Display merchant | Category | Subscription | Direction |
| --- | --- | --- | --- | --- |
| CVS PHARMACY | CVS Pharmacy | Health & Fitness | No | Expense |
| WALGREENS | Walgreens | Health & Fitness | No | Expense |
| PLANET FITNESS | Planet Fitness | Health & Fitness | Yes | Expense |
| CLASS PASS | ClassPass | Health & Fitness | Yes | Expense |
| PELOTON MEMBERSHIP | Peloton | Health & Fitness | Yes | Expense |

### Insurance

| Exact merchant key | Display merchant | Category | Subscription | Direction |
| --- | --- | --- | --- | --- |
| STATE FARM | State Farm | Insurance | No | Expense |
| GEICO | GEICO | Insurance | No | Expense |
| PROGRESSIVE | Progressive | Insurance | No | Expense |
| ALLSTATE | Allstate | Insurance | No | Expense |

Insurance is recurring spending but is not labeled Subscription in V1. Subscription is reserved for
cancelable membership/content/software services; recurring bills remain discoverable by PR7's
recurring-charge analysis.

### Education

| Exact merchant key | Display merchant | Category | Subscription | Direction |
| --- | --- | --- | --- | --- |
| COURSERA PLUS | Coursera Plus | Education | Yes | Expense |
| UDEMY | Udemy | Education | No | Expense |
| CHEGG STUDY | Chegg Study | Education | Yes | Expense |

### Travel

| Exact merchant key | Display merchant | Category | Subscription | Direction |
| --- | --- | --- | --- | --- |
| UNITED AIRLINES | United Airlines | Travel | No | Expense |
| AMERICAN AIRLINES | American Airlines | Travel | No | Expense |
| DELTA AIR LINES | Delta Air Lines | Travel | No | Expense |
| SOUTHWEST AIRLINES | Southwest Airlines | Travel | No | Expense |
| AIRBNB | Airbnb | Travel | No | Expense |
| MARRIOTT | Marriott | Travel | No | Expense |
| HILTON | Hilton | Travel | No | Expense |
| HERTZ | Hertz | Travel | No | Expense |
| ENTERPRISE RENT A CAR | Enterprise | Travel | No | Expense |

### Pets

| Exact merchant key | Display merchant | Category | Subscription | Direction |
| --- | --- | --- | --- | --- |
| PETCO | Petco | Pets | No | Expense |
| PETSMART | PetSmart | Pets | No | Expense |
| CHEWY | Chewy | Pets | No | Expense |

### Taxes, fees, and cash

| Exact merchant key | Display merchant | Category | Subscription | Direction |
| --- | --- | --- | --- | --- |
| IRS US TAX PAYMENT | IRS | Taxes & Fees | No | Expense |
| BANK SERVICE FEE | Bank Fee | Taxes & Fees | No | Expense |
| OVERDRAFT FEE | Overdraft Fee | Taxes & Fees | No | Expense |
| ATM FEE | ATM Fee | Taxes & Fees | No | Expense |
| ATM WITHDRAWAL | ATM Withdrawal | Cash & ATM | No | Expense |

Personal Care, Childcare, and Gifts & Donations remain useful built-in categories but intentionally
start without broad merchant rules. Those merchants are commonly local, generic, or context
dependent, so a workspace correction is safer than a system guess.

## Ambiguity policy

The following keys must not receive a built-in category without more specific information:

| Ambiguous key | Why no automatic category |
| --- | --- |
| PAYPAL | Can represent nearly any merchant, transfer, or reimbursement |
| VENMO | Can be dining, rent, gifts, reimbursements, or transfers |
| ZELLE | Can be rent, services, gifts, or transfers |
| CASH APP | Can be a purchase, transfer, or reimbursement |
| APPLE COM BILL | Can be entertainment, software, cloud storage, or a one-time app purchase |
| GOOGLE | Can be advertising, hardware, software, storage, or entertainment |
| SQUARE | Often identifies the payment processor rather than the merchant |
| STRIPE | Often identifies the payment processor rather than the merchant |

Mixed retailers such as Amazon, Walmart, Target, and Costco receive a broad Shopping default, but
never a Subscription label unless the canonical key names a subscription-specific product such as
`AMAZON PRIME`.

## Subscription behavior

PR5 adds `is_subscription: bool` to the categorization decision, transaction, and workspace merchant
rule. The transaction edit/review UI displays a Subscription checkbox independently from the
category picker.

Built-in rules set `is_subscription=True` only for a subscription-specific product or a service
whose normal charge is a cancelable membership. Utilities, insurance, rent, mortgages, loan
payments, and other recurring bills are not labeled Subscription. PR7 may identify them separately
as recurring charges.

A manual choice or workspace rule may set or clear Subscription. Saving for future stores the
merchant, category, and subscription choice together. A catalog update never retroactively changes
committed transactions.

## Rule maintenance

There is no V1 application-admin role and no global rule editor in the UI.

- Built-in categories and rules are maintained in source control and changed through reviewed code
  changes with tests.
- Any accepted workspace member may create a custom category, manually correct a transaction, and
  save a workspace merchant rule.
- Workspace rules affect only that workspace and override built-ins.
- A future admin/catalog service is unnecessary until multiple deployments need centralized catalog
  updates.

Every built-in catalog addition requires:

1. An anonymized exact description fixture or documented canonical key.
2. One primary category with a clear boundary.
3. An explicit subscription value.
4. An amount-direction constraint.
5. A test proving the exact key matches.
6. A test proving a similar ambiguous key does not match when that risk exists.

## Reporting behavior

Category summaries group every transaction once by primary category. Subscription summaries filter
the same transactions by `is_subscription=True`; they do not create a second expense.

The transaction list supports category and Subscription filters after PR4 provides its list/filter
interface. The import review shows both the suggested category and a Subscription badge/checkbox
before commit.

## Testing requirements

- Every built-in category name resolves to a seeded built-in `Category` row.
- Every catalog key is already canonical, unique, nonblank, and at most 255 characters.
- Every rule has exactly one valid category, subscription value, and direction.
- Exact aliases match; prefixes and similar strings do not.
- Expense-only rules do not categorize incoming deposits.
- Income-only rules do not categorize outgoing charges.
- Manual category/subscription choices survive later automatic evaluation.
- Workspace rules override built-ins for both category and subscription.
- The same merchant key can have different workspace results without leakage.
- Unknown and explicitly ambiguous keys fall back to Uncategorized and not Subscription.
- Category reports count a subscription transaction once, while the subscription filter can also
  find it.

## Scope boundaries

This catalog does not add fuzzy matching, regexes, merchant APIs, machine learning, automatic global
rule creation, or retroactive recategorization. PR7 remains responsible for discovering recurring
charge cadence from transaction history. PR5 supplies only the explicit Subscription label and
deterministic starting rules.

## Acceptance criteria

1. A new workspace can automatically categorize representative spending across groceries, dining,
   transportation, shopping, entertainment, software, health, insurance, education, travel, pets,
   utilities, housing, taxes/fees, cash, income, and transfers.
2. Eating out consistently appears under Dining & Drinks.
3. Known subscription products keep their primary category and also show Subscription.
4. Recurring bills such as rent, utilities, and insurance are not mislabeled as subscriptions.
5. Ambiguous payment processors and peer-to-peer payments remain Uncategorized.
6. Members can correct built-in results and save workspace-only rules.
7. Manual and workspace choices retain precedence over the built-in catalog.
8. Unknown descriptions fail safely to Uncategorized without blocking import review.
