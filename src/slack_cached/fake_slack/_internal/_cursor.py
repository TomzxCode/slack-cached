"""Base64 cursor encoding/decoding for pagination."""

import base64


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode()


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    return int(base64.urlsafe_b64decode(cursor.encode()).decode())
