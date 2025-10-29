"""ID helpers."""

import uuid


def new_id() -> str:
    """Return a freshly generated UUID4 string."""
    return str(uuid.uuid4())
