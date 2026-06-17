"""Parsing humanized duration strings (e.g. ``24h``, ``2d5h30m``, ``all``)."""

import re
from datetime import UTC, datetime, timedelta

_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)([dhms])", re.IGNORECASE)
_DURATION_UNITS = {"d": "days", "h": "hours", "m": "minutes", "s": "seconds"}


def _parse_duration(text: str) -> timedelta | None:
    """Parse a humanized duration string (e.g. ``24h``, ``2d5h30m``, ``90m``).

    Returns *None* for the special value ``"all"`` (meaning no limit).
    Raises ``ValueError`` on unrecognised input.
    """
    if text.lower() == "all":
        return None
    parts = _DURATION_RE.findall(text)
    if not parts or "".join(f"{v}{u}" for v, u in parts) != text:
        raise ValueError(f"invalid duration: {text!r}")
    kwargs: dict[str, float] = {}
    for value, unit in parts:
        kwargs[_DURATION_UNITS[unit.lower()]] = float(value)
    return timedelta(**kwargs)


def _oldest_ts_from_last(text: str) -> str | None:
    """Convert a --last duration string to an epoch-seconds string, or None."""
    delta = _parse_duration(text)
    if delta is None:
        return None
    oldest_dt = datetime.now(tz=UTC) - delta
    return f"{oldest_dt.timestamp():.6f}"
