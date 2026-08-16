from app.rules.presentation import describe_actions, describe_condition
from app.rules.service import RuleDraft
from app.rules.types import AllCondition, AnyCondition, NotCondition, PredicateCondition


def test_describe_condition_recursively_preserves_grouping_and_negation() -> None:
    """Break if a nested condition loses its ALL/ANY/NOT meaning in the summary."""
    condition = AllCondition(
        (
            PredicateCondition("description", "contains", "Coffee"),
            NotCondition(
                AnyCondition(
                    (
                        PredicateCondition("amount_cents", "greater_than", 2_000),
                        PredicateCondition("direction", "equal", "income"),
                    )
                )
            ),
        )
    )

    assert describe_condition(condition) == (
        "all of (description contains \u201cCoffee\u201d; "
        "not (any of (amount is greater than $20.00; direction is income)))"
    )


def test_describe_condition_uses_authorized_account_labels_when_available() -> None:
    """Break if account predicates expose only an opaque ID despite a supplied safe label."""
    condition = PredicateCondition("account_id", "equal", 17)

    assert describe_condition(condition, account_names={17: "Household checking"}) == (
        "account is \u201cHousehold checking\u201d"
    )


def test_describe_actions_includes_every_rule_action() -> None:
    """Break if the THEN summary silently omits merchant, tags, subscription, or cadence."""
    draft = RuleDraft(
        name="Coffee subscriptions",
        condition=PredicateCondition("description", "contains", "COFFEE"),
        normalized_merchant="Coffee Club",
        category_id=4,
        tag_ids=(7, 9),
        is_subscription=True,
        billing_period_months=3,
    )

    summary = describe_actions(
        draft,
        category_name="Dining & Drinks",
        tag_names=("Coffee", "Recurring"),
    )

    assert summary == (
        "set merchant to \u201cCoffee Club\u201d; set category to \u201cDining & Drinks\u201d; "
        "add tags \u201cCoffee\u201d, \u201cRecurring\u201d; mark as subscription; "
        "repeat every 3 months"
    )
