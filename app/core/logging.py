import logging


def configure_logging():
    """Configure a sane default logger for local development.

    Production deployments should replace or extend this configuration.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


logger = logging.getLogger("where_is_my_money")
