from datetime import UTC, datetime


def utcnow_naive():
    """Return current UTC timestamp as naive datetime for DB timestamp columns."""
    return datetime.now(UTC).replace(tzinfo=None)
