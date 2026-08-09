"""Signed import-review baselines for untrusted browser forms."""

from dataclasses import dataclass

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.imports.types import ReviewRow

REVIEW_TOKEN_SALT = "where-is-my-money-import-review"
REVIEW_TOKEN_MAX_AGE = 3600


class ReviewTokenError(ValueError):
    pass


@dataclass(frozen=True)
class ReviewBaseline:
    normalized_merchant: str | None
    category_id: int | None
    is_subscription: bool | None
    categorization_source: str | None


def create_review_token(secret_key: str, job_id: int, row: ReviewRow) -> str:
    serializer = URLSafeTimedSerializer(secret_key, salt=REVIEW_TOKEN_SALT)
    return serializer.dumps(
        {
            "v": 1,
            "job_id": job_id,
            "row_number": row.row_number,
            "normalized_merchant": row.normalized_merchant,
            "category_id": row.category_id,
            "is_subscription": row.is_subscription,
            "categorization_source": row.categorization_source,
        }
    )


def load_review_token(
    secret_key: str,
    token: str,
    job_id: int,
    row_number: int,
) -> ReviewBaseline:
    serializer = URLSafeTimedSerializer(secret_key, salt=REVIEW_TOKEN_SALT)
    try:
        payload = serializer.loads(token, max_age=REVIEW_TOKEN_MAX_AGE)
    except (BadSignature, SignatureExpired, TypeError) as exc:
        raise ReviewTokenError("Review data could not be verified; reload and try again.") from exc

    if not isinstance(payload, dict):
        raise ReviewTokenError("Review data could not be verified; reload and try again.")
    merchant = payload.get("normalized_merchant")
    category_id = payload.get("category_id")
    subscription = payload.get("is_subscription")
    source = payload.get("categorization_source")
    valid = (
        payload.get("v") == 1
        and payload.get("job_id") == job_id
        and payload.get("row_number") == row_number
        and (merchant is None or isinstance(merchant, str))
        and (category_id is None or type(category_id) is int)
        and (subscription is None or type(subscription) is bool)
        and (source is None or isinstance(source, str))
    )
    if not valid:
        raise ReviewTokenError("Review data could not be verified; reload and try again.")
    return ReviewBaseline(merchant, category_id, subscription, source)
