"""Workspace configuration: WorkspaceParams dataclass and CLI flag parsing."""

from dataclasses import dataclass

from slack_cached.fake_slack._internal._constants import DEFAULT_EPOCH_BASE


@dataclass
class WorkspaceParams:
    """Controls the size and shape of the generated fake workspace."""

    seed: int = 42
    num_users: int = 20
    num_channels: int = 13
    num_ims: int = 4
    num_threads: int = 30
    min_messages_per_thread: int = 3
    max_messages_per_thread: int = 12
    activity_ratio: float = 0.6
    rate_limits: bool = False
    epoch_base: float = DEFAULT_EPOCH_BASE


def _parse_range_flag(raw: str) -> tuple[int, int]:
    """Parse an ``N`` or ``N-M`` flag into (min, max)."""
    if "-" in raw:
        parts = raw.split("-", 1)
        return int(parts[0].strip()), int(parts[1].strip())
    val = int(raw.strip())
    return val, val


def _parse_epoch_base(raw: str | None) -> float:
    """Parse the --epoch-base flag value into a unix timestamp."""
    if raw is None:
        return DEFAULT_EPOCH_BASE
    from datetime import UTC, datetime

    raw = raw.strip()
    if raw.lower() == "now":
        return datetime.now(tz=UTC).timestamp()
    try:
        return float(raw)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(raw, fmt).replace(tzinfo=UTC)
            return dt.timestamp()
        except ValueError:
            continue
    raise ValueError(f"cannot parse --epoch-base value: {raw!r}")
