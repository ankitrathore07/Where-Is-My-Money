"""Google OpenID Connect client construction."""

from authlib.integrations.starlette_client import OAuth

from app.core.config import Settings

GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"


def build_google_oauth(configured: Settings) -> OAuth:
    """Register Google's discovery-based OpenID Connect client."""
    oauth = OAuth()
    oauth.register(
        name="google",
        client_id=configured.google_client_id,
        client_secret=configured.google_client_secret,
        server_metadata_url=GOOGLE_DISCOVERY_URL,
        client_kwargs={"scope": "openid email profile"},
    )
    return oauth
