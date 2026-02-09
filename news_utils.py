import hashlib
from typing import Any


def generate_numeric_id(value: Any, digest_bytes: int = 8) -> int:
    """Generate a stable numeric ID from the input value."""
    if value is None:
        value = ""
    if not isinstance(value, str):
        value = str(value)

    if digest_bytes <= 0 or digest_bytes > 32:
        raise ValueError("digest_bytes must be between 1 and 32.")

    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return int.from_bytes(digest[:digest_bytes], "big", signed=False)
