"""Data generation: users, channels, and threads from template pools."""

import random
from typing import Any

import structlog

from slack_cached.fake_slack._internal._config import WorkspaceParams
from slack_cached.fake_slack._internal._constants import TEAM_ID
from slack_cached.fake_slack._internal._data_pools import (
    _CHANNEL_DEFS,
    _CHANNEL_TOPIC_MAP,
    _FIRST_NAMES,
    _LAST_NAMES,
    _ROLES,
)

log = structlog.get_logger(__name__)


def _generate_users(rng: random.Random, params: WorkspaceParams) -> list[dict[str, Any]]:
    colors = ["9f69e7", "e7a34f", "4f9fe7", "e74f9f", "69e74f", "e7694f", "e74f4f", "4fe7e7"]
    timezones = [
        ("America/New_York", "Eastern Standard Time", -18000),
        ("America/Los_Angeles", "Pacific Standard Time", -28800),
        ("Europe/London", "Greenwich Mean Time", 0),
        ("Europe/Berlin", "Central European Time", 3600),
        ("Asia/Tokyo", "Japan Standard Time", 32400),
        ("Asia/Kolkata", "India Standard Time", 19800),
    ]
    chosen_first = _FIRST_NAMES.copy()
    chosen_last = _LAST_NAMES.copy()
    rng.shuffle(chosen_first)
    rng.shuffle(chosen_last)
    pairs = list(zip(chosen_first, chosen_last, strict=False))
    available_roles = _ROLES.copy()
    rng.shuffle(available_roles)

    users: list[dict[str, Any]] = []
    for i in range(params.num_users):
        first, last = pairs[i % len(pairs)]
        role_pool = available_roles * ((i // len(available_roles)) + 1)
        role = role_pool[i]
        is_admin = rng.random() < 0.1
        name = f"{first.lower()}.{last.lower()}"
        uid = f"U{i + 1:04d}"
        if rng.random() < 0.05:
            uid = f"U01BOT{i + 1:04d}"
            is_admin = False
            role = "Integration Service"
        tz = rng.choice(timezones)

        user: dict[str, Any] = {
            "id": uid,
            "team_id": TEAM_ID,
            "name": name,
            "deleted": False,
            "color": rng.choice(colors),
            "real_name": f"{first} {last}",
            "tz": tz[0],
            "tz_label": tz[1],
            "tz_offset": tz[2],
            "profile": {
                "title": role,
                "phone": "",
                "skype": "",
                "real_name": f"{first} {last}",
                "real_name_normalized": f"{first} {last}",
                "display_name": first,
                "display_name_normalized": first,
                "fields": None,
                "status_text": "",
                "status_emoji": "",
                "status_expiration": 0,
                "avatar_hash": f"g{rng.randint(1000000000, 9999999999)}",
                "email": f"{name}@acme.io",
                "team": TEAM_ID,
            },
            "is_admin": is_admin,
            "is_owner": False,
            "is_primary_owner": False,
            "is_restricted": False,
            "is_ultra_restricted": False,
            "is_bot": "BOT" in uid,
            "is_app_user": "BOT" in uid,
            "updated": params.epoch_base + rng.randint(0, 86400 * 60),
            "is_email_confirmed": "BOT" not in uid,
        }
        users.append(user)
    return users


def _generate_channels(rng: random.Random, params: WorkspaceParams) -> list[dict[str, Any]]:
    # Use the first params.num_channels from the pool, then cycle if needed
    channels: list[dict[str, Any]] = []
    for i in range(params.num_channels):
        def_idx = i % len(_CHANNEL_DEFS)
        name, is_private, purpose_text = _CHANNEL_DEFS[def_idx]
        suffix = "" if i < len(_CHANNEL_DEFS) else f"-{i // len(_CHANNEL_DEFS) + 1}"
        cid = f"C{i + 1:04d}"

        channel: dict[str, Any] = {
            "id": cid,
            "name": f"{name}{suffix}",
            "name_normalized": f"{name}{suffix}",
            "is_channel": not is_private,
            "is_group": False,
            "is_im": False,
            "is_mpim": False,
            "is_private": is_private,
            "is_archived": False,
            "is_general": name == "general",
            "is_shared": False,
            "is_org_shared": False,
            "is_pending_ext_shared": False,
            "pending_shared": [],
            "context_team_id": TEAM_ID,
            "updated": params.epoch_base + rng.randint(0, 86400 * 60),
            "parent_conversation": None,
            "creator": f"U{rng.randint(1, params.num_users):04d}",
            "is_ext_shared": False,
            "shared_team_ids": [TEAM_ID],
            "pending_connected_team_ids": [],
            "is_member": True,
            "last_read": f"{params.epoch_base + rng.randint(0, 86400 * 7):.6f}",
            "topic": {"value": "", "creator": "", "last_set": 0},
            "purpose": {
                "value": purpose_text,
                "creator": f"U{rng.randint(1, params.num_users):04d}",
                "last_set": int(params.epoch_base),
            },
            "created": int(params.epoch_base),
            "num_members": rng.randint(3, 50),
        }
        channels.append(channel)
    return channels


def _fill_template(template: str, rng: random.Random, context: dict[str, str]) -> str:
    """Replace {placeholders} in *template* using the *context* dict.

    Unknown placeholders are filled with a random value drawn from built-in
    vocab lists so every template can be self-sufficient.
    """
    result = template
    for key, value in context.items():
        result = result.replace(f"{{{key}}}", value)
    return result


def _generate_threads(
    rng: random.Random,
    params: WorkspaceParams,
    channels: list[dict[str, Any]],
    users: list[dict[str, Any]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    threads: dict[tuple[str, str], list[dict[str, Any]]] = {}
    user_ids = [u["id"] for u in users if not u.get("is_bot")]
    if not user_ids:
        user_ids = [u["id"] for u in users]

    channel_lookup = {c["name"]: c["id"] for c in channels}

    # Determine which channel names are available
    available_channel_names = [c["name"] for c in channels]
    # Map template channel names to available channels (try exact match first)
    # Build routes from engineering/design/etc to actual channel IDs

    # Build a mapping from generic channel names to actual IDs
    def _channel_id(channel_name: str) -> str | None:
        if channel_name in channel_lookup:
            return channel_lookup[channel_name]
        return None

    # Collect all templates keyed by available channel names
    channel_templates: dict[str, list[list[tuple[int, str]]]] = {}
    for ch_name in available_channel_names:
        for generic_name, patterns in _CHANNEL_TOPIC_MAP.items():
            if ch_name.startswith(generic_name) or (generic_name == ch_name):
                channel_templates.setdefault(ch_name, []).extend(patterns)

    # For channels with no specific templates, use a fallback
    fallback_templates = _CHANNEL_TOPIC_MAP.get("random", [])

    # Compute activity weights for users (power law distribution)
    activity_weights: list[float] = []
    for _ in user_ids:
        w = -rng.random()
        activity_weights.append(w)
    min_w = min(activity_weights)
    activity_weights = [1.0 + (w - min_w) * 2.0 for w in activity_weights]
    # Sort users by activity for assigning to threads
    active_users = list(zip(user_ids, activity_weights, strict=False))
    active_users.sort(key=lambda x: x[1], reverse=True)
    top_n = max(3, int(len(active_users) * params.activity_ratio))
    primary_users = [u for u, _ in active_users[:top_n]]

    # Build vocab for placeholder filling
    _TECH_WORDS = {
        "tech": [
            "Redis",
            "PostgreSQL",
            "MongoDB",
            "DynamoDB",
            "Kafka",
            "Elasticsearch",
            "RabbitMQ",
            "S3",
            "Docker",
            "Kubernetes",
            "Terraform",
        ],
        "services": [
            "auth",
            "payment",
            "user",
            "notification",
            "order",
            "search",
            "inventory",
            "analytics",
            "billing",
            "shipping",
        ],
        "features": [
            "rate limiting",
            "auth migration",
            "API design",
            "design system",
            "SSO",
            "reporting",
            "search",
            "notifications",
            "dashboards",
        ],
        "frameworks": [
            "React",
            "Django",
            "FastAPI",
            "Rails",
            "Next.js",
            "Flask",
            "Express",
            "Spring",
            "Vue",
            "Svelte",
        ],
        "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
    }

    def _vocab(key: str) -> str:
        pool = _TECH_WORDS.get(key)
        if pool:
            return rng.choice(pool)
        return f"{key}-{rng.randint(100, 999)}"

    def _generate_placeholder(key: str, ctx: dict[str, str]) -> str:
        if key in ctx:
            return ctx[key]
        # Generate a context-appropriate value
        if key in ("n", "m", "count_dependent"):
            return str(rng.randint(2, 8))
        if key in ("time",):
            return "10am" if rng.random() < 0.5 else "2pm"
        if key.startswith("number") or key == "pr_number":
            return str(rng.randint(1000, 9999))
        if key == "version":
            return f"{rng.randint(1, 5)}.{rng.randint(0, 9)}"
        if key == "pct":
            return str(rng.randint(5, 50))
        if key.endswith("_time") and "start" not in key and "end" not in key:
            return f"{rng.uniform(0.2, 5.0):.1f}"
        if key == "error_pct":
            return f"{rng.uniform(0.5, 5.0):.1f}"
        if key == "before_size":
            return f"{rng.uniform(1.0, 5.0):.1f}"
        if key == "after_size":
            return f"{rng.uniform(5.0, 20.0):.1f}"
        if key == "final_size":
            return f"{rng.uniform(1.5, 3.0):.1f}"
        if key.endswith("_number") or key.startswith("sprint_"):
            return str(rng.randint(1, 30))
        if key.startswith("first_"):
            return rng.choice(_FIRST_NAMES)
        if key in ("new_member",):
            return f"{rng.choice(_FIRST_NAMES)} {rng.choice(_LAST_NAMES)}"
        if key in ("buddy", "person", "primary", "secondary", "escalation"):
            return rng.choice(user_ids) if rng.random() < 0.5 else rng.choice(_FIRST_NAMES)
        if key in ("place",):
            return rng.choice(
                [
                    "Sushi Bar",
                    "Ramen House",
                    "Pizza Place",
                    "Taco Spot",
                    "Burger Joint",
                    "Pho Kitchen",
                    "BBQ Shack",
                ]
            )
        if key in ("street",):
            return rng.choice(["Main St", "Oak Ave", "Broadway", "Market St", "Park Ave", "Elm St"])
        if key in ("dish", "alternative_dish"):
            return rng.choice(
                ["ramen", "tacos", "pizza", "sushi", "bibimbap", "pho", "burger", "pad thai"]
            )
        if key in ("show", "other_show"):
            return rng.choice(
                [
                    "The Expanse",
                    "Severance",
                    "Foundation",
                    "Andor",
                    "Silo",
                    "3 Body Problem",
                    "Fallout",
                    "Slow Horses",
                    "Mindhunter",
                ]
            )
        if key in ("season", "episode", "n_issues"):
            return str(rng.randint(1, 10))
        if key in ("option_a", "option_b", "option_c"):
            return rng.choice(_TECH_WORDS.get("tech", ["Option A"]))

        # Last resort: use a tech word
        return _vocab("tech")

    # Generate threads
    thread_count = 0
    while thread_count < params.num_threads:
        # Pick a channel
        ch_name = rng.choice(available_channel_names)
        ch_id = channel_lookup.get(ch_name, channels[0]["id"])

        # Pick a pattern
        patterns = channel_templates.get(ch_name, fallback_templates)
        if not patterns:
            continue
        pattern: list[tuple[int, str]] = rng.choice(patterns)

        # Determine the number of messages for this thread
        msg_count = rng.randint(params.min_messages_per_thread, params.max_messages_per_thread)
        # Truncate pattern to msg_count (but at least 1)
        effective_pattern = pattern[: max(1, msg_count)]

        # Pick originator and participants
        originator = rng.choice(primary_users)
        other_primary = [u for u in primary_users if u != originator]
        if len(other_primary) < 3:
            other_primary = [u for u in user_ids if u != originator]
        rng.shuffle(other_primary)
        participants = [originator] + other_primary[:4]

        # Build context for placeholder filling
        context: dict[str, str] = {}

        def _ctx_val(template: str, known: dict[str, str]) -> str:
            result = template
            for k, v in known.items():
                result = result.replace(f"{{{k}}}", v)
            return result

        # Pre-fill some common placeholders
        for key in [
            "event",
            "artifact",
            "topic",
            "role",
            "component",
            "old_solution",
            "new_solution",
            "problem",
            "benefit",
            "concern",
            "fallback",
            "feature",
            "approach",
            "alternative",
            "edge_case",
            "issue",
            "area",
            "service",
            "endpoint",
            "error_type",
            "cause",
            "affected_resource",
            "root_cause",
            "model_name",
            "framework",
            "package",
            "categories",
            "environment",
            "change_1",
            "change_2",
            "change_3",
            "feature_1",
            "feature_2",
            "feature_3",
            "phase",
            "improve_1",
            "improve_2",
            "improve_3",
            "positive_1",
            "positive_2",
            "positive_3",
            "action_1",
            "action_2",
            "action_3",
            "extra_theme",
            "theme_1",
            "theme_2",
            "theme_3",
            "design_need",
            "project",
            "use_case",
            "product",
            "metric",
            "component_name",
            "incident_type",
            "ip_range",
            "exploit_type",
            "vulnerable_component",
            "proposed_rfc",
            "validation_tests",
            "ci_cd",
            "registry",
            "storage",
            "fix_type",
            "bad_import",
            "bad_practice",
            "fix_approach",
            "issue_area",
            "issue_1",
            "issue_2",
            "issue_3",
            "option_a",
            "option_b",
            "other_option",
            "alternative_dish",
            "place",
            "street",
            "dish",
            "show",
            "other_show",
            "season",
            "volume",
            "limit",
            "latency_threshold",
            "error_threshold",
            "rate_threshold",
            "wcag_level",
            "planned_components",
            "improvement_factor",
        ]:
            if key not in context:
                context[key] = _generate_placeholder(key, context)

        base_ts = params.epoch_base + (thread_count * 3600 * 4)
        thread_ts = f"{base_ts:.6f}"

        fake_messages: list[dict[str, Any]] = []
        for j, (speaker_idx, template) in enumerate(effective_pattern):
            gap = rng.uniform(15, 600)
            msg_ts = base_ts if j == 0 else float(fake_messages[-1]["ts"]) + gap

            speaker_id = participants[speaker_idx % len(participants)]
            text = _fill_template(template, rng, context)

            msg: dict[str, Any] = {
                "type": "message",
                "user": speaker_id,
                "text": text,
                "ts": f"{msg_ts:.6f}",
                "thread_ts": thread_ts,
                "blocks": [],
                "files": [],
                "upload": False,
                "display_as_bot": False,
                "is_starred": False,
                "source_team": TEAM_ID,
                "user_team": TEAM_ID,
            }
            if j > 0:
                msg["parent_user_id"] = fake_messages[0]["user"]
            # Some thread parents are edited. Real Slack surfaces the ``edited``
            # field on conversations.replies/history but omits it from
            # search.messages; mirroring that here exercises the canonical
            # payload comparison in storage.upsert_messages.
            if j == 0 and rng.random() < 0.25:
                edit_ts = float(msg["ts"]) + rng.uniform(60, 3600)
                msg["edited"] = {"ts": f"{edit_ts:.6f}", "user": speaker_id}
            fake_messages.append(msg)

        threads[(ch_id, thread_ts)] = fake_messages
        thread_count += 1

    return threads
