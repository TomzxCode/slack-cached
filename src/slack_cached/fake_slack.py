"""Fake Slack API server returning stable, realistic workspace data.

Start the server::

    python -m slack_cached.fake_slack --port 8199 --seed 42

Exposes Slack Web API endpoints under ``/api/``:

    GET /api/conversations.list
    GET /api/conversations.replies
    GET /api/conversations.history
    GET /api/users.list

All data is deterministically generated from a seed so the same seed always
produces the same workspace.  Cursor-based pagination is fully supported.

Pass ``--rate-limits`` to enable per-endpoint rate limiting that mirrors
Slack's documented tiers (disabled by default).  Rate-limited requests
receive HTTP 429 with a ``Retry-After`` header.

Configuration parameters (``WorkspaceParams`` / CLI flags):

    --num-users N
        Number of workspace members (default 20).
    --num-channels N
        Number of conversations/channels (default 13).
    --num-threads N
        Number of conversation threads (default 30).
    --messages-per-thread N or N-M
        Message count per thread (default 3-12).
    --activity-ratio R
        Fraction of users who actively participate (default 0.6).
    --rate-limits
        Enable Slack-compatible rate limiting (disabled by default).
"""

from __future__ import annotations

import argparse
import base64
import json
import random
import sys
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import structlog

log = structlog.get_logger(__name__)

TEAM_ID = "T01FAKEWK"
DEFAULT_EPOCH_BASE = 1704067200.0

ENDPOINT_RATE_LIMITS: dict[str, int] = {
    "conversations.replies": 50,
    "conversations.history": 50,
    "users.list": 20,
    "conversations.list": 20,
}

RATE_LIMIT_WINDOW = 60.0


class RateLimiter:
    """Thread-safe sliding-window rate limiter per API endpoint.

    Uses Slack's documented tier limits.  Only successful (non-429) requests
    count against the window so that retries after a ``Retry-After`` sleep
    don't cascade.
    """

    def __init__(
        self,
        limits: dict[str, int] | None = None,
        window: float = RATE_LIMIT_WINDOW,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._limits = limits if limits is not None else dict(ENDPOINT_RATE_LIMITS)
        self._window = window
        self._now = now or time.time
        self._request_times: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def check(self, path: str) -> tuple[bool, int]:
        """Check whether *path* is within its rate limit.

        Returns ``(allowed, retry_after)``.  When *allowed* is ``False``,
        *retry_after* is the number of seconds the client should wait.
        """
        endpoint = self._endpoint_for_path(path)
        if endpoint is None:
            return True, 0

        limit = self._limits[endpoint]
        now = self._now()

        with self._lock:
            times = [t for t in self._request_times[endpoint] if now - t < self._window]
            self._request_times[endpoint] = times

            if len(times) >= limit:
                oldest = min(times)
                retry_after = max(1, int(self._window - (now - oldest)))
                return False, retry_after

            self._request_times[endpoint].append(now)
            return True, 0

    def _endpoint_for_path(self, path: str) -> str | None:
        for endpoint in self._limits:
            if endpoint in path:
                return endpoint
        return None


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode()


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    return int(base64.urlsafe_b64decode(cursor.encode()).decode())


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class WorkspaceParams:
    """Controls the size and shape of the generated fake workspace."""

    seed: int = 42
    num_users: int = 20
    num_channels: int = 13
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


# ---------------------------------------------------------------------------
# Name pools
# ---------------------------------------------------------------------------

_FIRST_NAMES: list[str] = [
    "Sarah",
    "Marcus",
    "Priya",
    "David",
    "Emily",
    "James",
    "Yuki",
    "Alex",
    "Maria",
    "Chris",
    "Aisha",
    "Tomas",
    "Lisa",
    "Ryan",
    "Zara",
    "Ben",
    "Hannah",
    "Nikita",
    "Rachel",
    "Omar",
    "Sophie",
    "Miguel",
    "Aiko",
    "Kwame",
    "Elena",
    "Lucas",
    "Mei",
    "Oliver",
    "Fatima",
    "Diego",
]

_LAST_NAMES: list[str] = [
    "Chen",
    "Williams",
    "Patel",
    "Kim",
    "Rodriguez",
    "O'Brien",
    "Tanaka",
    "Thompson",
    "Santos",
    "Johnson",
    "Mohammed",
    "Garcia",
    "Nguyen",
    "Murphy",
    "Ali",
    "Cooper",
    "Lee",
    "Petrov",
    "Green",
    "Hassan",
    "Weber",
    "Sato",
    "Kowalski",
    "Okafor",
    "Mueller",
    "Voss",
    "Thorne",
    "Delgado",
    "Rizzo",
    "Fontaine",
]

_ROLES: list[str] = [
    "Engineering Lead",
    "Backend Engineer",
    "Frontend Engineer",
    "DevOps Engineer",
    "Product Manager",
    "Product Designer",
    "ML Engineer",
    "QA Engineer",
    "Full Stack Engineer",
    "Security Engineer",
    "Data Engineer",
    "Engineering Manager",
    "SRE",
    "Platform Engineer",
    "Technical Writer",
    "Data Scientist",
]

# ---------------------------------------------------------------------------
# Channel definitions
# ---------------------------------------------------------------------------

_CHANNEL_DEFS: list[tuple[str, bool, str]] = [
    ("general", False, "Company-wide announcements and work-based matters"),
    ("random", False, "Non-work banter and water cooler conversation"),
    ("engineering", False, "Engineering team discussions"),
    ("design", False, "Design team discussions and reviews"),
    ("product", False, "Product discussions and planning"),
    ("announcements", False, "Important company announcements"),
    ("backend", False, "Backend engineering discussions"),
    ("frontend", False, "Frontend engineering discussions"),
    ("devops", False, "DevOps and infrastructure"),
    ("data-science", False, "Data science and analytics"),
    ("ml-ops", True, "ML operations and model deployment"),
    ("security-incidents", True, "Security incident response"),
    ("team-alpha", True, "Team Alpha private channel"),
]

# ---------------------------------------------------------------------------
# Conversation templates
# ---------------------------------------------------------------------------

_CHANNEL_TOPIC_MAP: dict[str, list[list[tuple[int, str]]]] = {
    "general": [
        [
            (
                0,
                "Good morning team! Quick reminder: {event} is today at {time}. Please have your priorities ready.",
            ),
            (1, "I've prepared the {artifact}. Will share the link before the meeting."),
            (2, "Looking forward to it! Should we also discuss {topic}?"),
            (3, "Absolutely, let's add that to the agenda."),
        ],
        [
            (0, "Please welcome {new_member} who joins us today as a {role}!"),
            (1, "Welcome {first_name}! Looking forward to working with everyone."),
            (2, "Welcome aboard! Your onboarding buddy is {buddy}. They'll help get you set up."),
            (1, "Thanks everyone for the warm welcome!"),
        ],
    ],
    "random": [
        [
            (0, "Anyone tried the new {place} on {street}? The {dish} is incredible."),
            (1, "Yes! Went there last week. The wait was about {minutes} min though."),
            (2, "Their {alternative_dish} is also really good. Fair warning: it's actually spicy."),
            (1, "Friday lunch crew: {place} then {activity}?"),
            (2, "I'm in."),
        ],
        [
            (0, "Has anyone watched {show}? The {season} was wild."),
            (2, "Yes! Just finished {episode}. The twist at the end was incredible."),
            (1, "No spoilers please! I'm only on episode {n}."),
            (0, "Don't worry. But definitely keep watching."),
            (2, "Much better than the last season of {other_show}. The writing is tighter."),
        ],
    ],
    "engineering": [
        [
            (
                0,
                "I'm proposing we migrate {component} from {old_solution} to {new_solution}. Here's why:",
            ),
            (
                1,
                "The main issues with {old_solution} are {problem}. {new_solution} addresses this with {benefit}.",
            ),
            (2, "What's the migration path? We have {count_dependent} services depending on this."),
            (
                0,
                "Phase 1: deploy {new_solution} alongside {old_solution}. Phase 2: migrate services one by one. Phase 3: decommission.",
            ),
            (3, "I'm concerned about {concern}. Have we evaluated that?"),
            (0, "Addressed in the RFC. The fallback plan is {fallback}."),
            (
                4,
                "Let's discuss this in the architecture review on {day}. Please prepare an RFC by then.",
            ),
        ],
        [
            (
                0,
                "PR #{pr_number} is ready for review: adds {feature} to {component}. Looking for {n} reviewers.",
            ),
            (1, "Quick question: why did you go with {approach} instead of {alternative}?"),
            (0, "{approach} handles {edge_case} better. The alternative would cause {issue}."),
            (2, "I'm seeing a potential issue with {concern}. Added a comment in the PR."),
            (0, "Good catch. Fixed in the latest push. Added a test for that scenario."),
            (1, "LGTM overall. Left a few minor comments on {area}. Approved."),
            (0, "Thanks both! Merging now."),
        ],
        [
            (0, "Sprint {sprint_number} retrospective: what went well and what to improve."),
            (1, "Went well: {positive_1}. Could improve: {improve_1}."),
            (2, "Went well: {positive_2}. Could improve: {improve_2}."),
            (3, "Went well: {positive_3}. Could improve: {improve_3}."),
            (0, "Action items:\n1. {action_1}\n2. {action_2}\n3. {action_3}"),
        ],
        [
            (
                0,
                "Feature flags discussion: should we use {option_a}, {option_b}, or build our own?",
            ),
            (1, "{option_b} is open source and has a solid feature set. We can self-host."),
            (2, "{option_a}'s UX is better but the per-seat pricing adds up at our scale."),
            (
                3,
                "For our use case ({use_case}), {option_b} covers everything. I've used it before.",
            ),
            (0, "Decision: {option_b}, self-hosted. {person}, can you set up the infrastructure?"),
            (1, "On it. I'll have a staging instance ready by next {day}."),
        ],
    ],
    "design": [
        [
            (
                0,
                "Sharing the updated design system components. Major changes: {change_1}, {change_2}, and {change_3}.",
            ),
            (1, "The new {component} looks much better. The contrast ratios meet WCAG AA?"),
            (0, "Yes, all checked. AAA for normal text, AA for large text."),
            (2, "Will the old component names be deprecated?"),
            (
                0,
                "Yes, we'll support old names for {n} sprints with console warnings, then remove them.",
            ),
            (
                1,
                "The spacing scale change will require updates to a lot of existing pages. Should we create a codemod?",
            ),
            (0, "Great idea. I'll create a ticket for that."),
        ],
        [
            (
                0,
                "Accessibility audit results for {feature}: {n_issues} issues found, {n_critical} critical.",
            ),
            (0, "Critical:\n- {issue_1}\n- {issue_2}\n- {issue_3}"),
            (1, "I can fix the {issue_area}. It's likely a matter of {fix_approach}."),
            (
                2,
                "Thanks! I've created tickets for each issue with reproduction steps. Targeting fix by end of week.",
            ),
            (
                3,
                "This is important. Let's make sure these are in the current sprint. I'll adjust priorities.",
            ),
        ],
    ],
    "product": [
        [
            (
                0,
                "Q{quarter} roadmap draft is ready for feedback. Key themes: {theme_1}, {theme_2}, and {theme_3}.",
            ),
            (1, "For engineering, {theme_2} is the top priority. Our {metric} needs work."),
            (2, "Agreed. I'd add {extra_theme} to the list. We're falling behind on it."),
            (
                3,
                "From design perspective: we need to invest in {design_need}. It's blocking velocity.",
            ),
            (4, "Good input. I'll incorporate these and share an updated version by EOD {day}."),
        ],
        [
            (
                0,
                "We need to decide on a vendor for {project}. Options: {option_a}, {option_b}, or self-hosted.",
            ),
            (
                1,
                "{option_a} is expensive but the UX is hard to beat. {option_b} is a good middle ground.",
            ),
            (2, "Self-hosted gives us the most control but the maintenance burden is significant."),
            (3, "I've used {option_b} at my previous company. It worked well for our scale."),
            (
                0,
                "Let's do a 2-week trial of {option_b}. {person}, can you set up a proof of concept?",
            ),
            (1, "On it. Will have the POC ready by next {day}."),
        ],
    ],
    "announcements": [
        [
            (0, "We're excited to announce the release of {product} v{version}! Key changes:"),
            (0, "- {feature_1}\n- {feature_2}\n- {feature_3}"),
            (
                0,
                "Release notes are available in our docs. Previous version will be supported until {date}.",
            ),
            (1, "The {feature_2} is a huge improvement. Our {metric} is 3x better now."),
            (2, "Great work everyone! This has been a big effort across the team."),
        ],
        [
            (
                0,
                "Planned maintenance window: {day} {start_time}-{end_time}. {service} upgrade. Expected downtime: {downtime}.",
            ),
            (1, "Runbook is prepared. We'll do a dry run on staging tonight."),
            (
                2,
                "On-call rotation for the maintenance: {primary} (primary), {secondary} (secondary). Escalation: {escalation}.",
            ),
            (3, "Customer notification has been sent. Support team is briefed."),
        ],
    ],
    "backend": [
        [
            (
                0,
                "Production alert: {endpoint} returning {error_type} errors at ~{error_pct}%. Investigating.",
            ),
            (
                1,
                "Checking the logs. Looks like {cause} is returning unexpected values for {affected_resource}.",
            ),
            (
                2,
                "I can see it too. The {component} is hitting a cache miss path that doesn't handle the {edge_case}.",
            ),
            (0, "Found it. {root_cause} changed their response format in v{version}."),
            (1, "Quick fix: adding a fallback that handles both formats. Deploying in 5 min."),
            (
                0,
                "Fix deployed. Error rate back to 0%. I'll add a schema validation test to catch this earlier.",
            ),
            (2, "Postmortem tomorrow at {time}. Please prepare the timeline."),
        ],
        [
            (0, "Database query optimization results for the {endpoint} endpoint:"),
            (
                0,
                "Before: {before_time}s average, {before_queries} queries\nAfter: {after_time}s average, {after_queries} queries",
            ),
            (
                1,
                "{improvement_factor}x improvement is impressive. Did you check the write performance impact?",
            ),
            (
                0,
                "Write throughput decreased by ~{write_impact}% which is within our acceptable range.",
            ),
            (
                2,
                "Great work. Let's apply the same approach to the {other_endpoint} endpoint next sprint.",
            ),
        ],
        [
            (
                0,
                "Designing the new {endpoint} endpoint. Should we support filtering and sorting from day one?",
            ),
            (
                1,
                "Yes. Start with: filter by {filter_field_1}, {filter_field_2}. Sort by {sort_field_1}, {sort_field_2}.",
            ),
            (
                2,
                "For filtering, I'd suggest the pattern: `?filter[{role}]={value}`. It's extensible.",
            ),
            (1, "Don't forget to include `has_more` in the response for UI decisions."),
            (0, "What about the default page size?"),
            (1, "25 default, max 100. Aligns with our other list endpoints."),
        ],
    ],
    "frontend": [
        [
            (0, "Starting the {framework} migration this sprint. Here's the plan:"),
            (
                0,
                "1. Update dependencies ({n}d)\n2. Migrate components ({n}d)\n3. Update tests ({n}d)\n4. Performance testing ({n}d)\n5. Staged rollout",
            ),
            (
                1,
                "The biggest risk is our third-party libraries. Some haven't announced support yet.",
            ),
            (
                0,
                "I've audited all dependencies. Only {n} are problematic. I've prepared drop-in replacements.",
            ),
            (
                2,
                "Should we use the new {feature_name} features during the migration or keep it incremental?",
            ),
            (0, "Incremental. We'll migrate first, then adopt new features in follow-up PRs."),
        ],
        [
            (0, "The new component library is now at v1.0! Install with `npm install {package}`."),
            (1, "Great milestone! How many components are included?"),
            (0, "{n} components across {m} categories: {categories}."),
            (2, "All components support theming, dark mode, and meet WCAG {wcag_level} standards."),
            (1, "The Storybook docs are really helpful. Nice job on the interactive examples."),
            (
                0,
                "Thanks! Feedback welcome. We're already working on v1.1 with {planned_components}.",
            ),
        ],
    ],
    "devops": [
        [
            (0, "Deployment to {environment} failed. Error: {error_type} during {phase}."),
            (1, "The build container has {memory} RAM. Is the asset bundle growing that large?"),
            (
                0,
                "Checking... the bundle size went from {before_size}MB to {after_size}MB after the update.",
            ),
            (2, "That's likely because we're importing {bad_import}. Let me fix the {fix_type}."),
            (
                2,
                "Fixed in PR #{pr_number}. Bundle is back to {final_size}MB with selective imports.",
            ),
            (0, "Deployment succeeded. Promoting to production."),
            (0, "Production deployment complete. All health checks passing."),
        ],
        [
            (
                0,
                "CI pipeline optimization results: build time reduced from {before_time} min to {after_time} min.",
            ),
            (0, "Changes: {change_1}, {change_2}, {change_3}."),
            (1, "Can we also parallelize the integration tests? They're the slowest part now."),
            (
                0,
                "Working on it. Need to set up isolated test databases for each shard. Should have it done this week.",
            ),
            (
                2,
                "I can help with the test database provisioning. We have a Terraform module for ephemeral DBs.",
            ),
        ],
        [
            (0, "Setting up alerting for the new {service} service. What thresholds make sense?"),
            (
                1,
                "For {service}: P99 latency > {latency_threshold}ms, error rate > {error_threshold}%.",
            ),
            (
                2,
                "Also add: successful {metric} rate < {rate_threshold}%. That's a business metric but critical to monitor.",
            ),
            (
                0,
                "What about notification channels? PagerDuty for error rate, Slack for latency warnings?",
            ),
            (
                1,
                "Yes. And a daily digest email for the business metrics. {team} team asked for that.",
            ),
            (0, "Configured. Testing the alerts now with synthetic traffic."),
        ],
    ],
    "data-science": [
        [
            (
                0,
                "The analytics pipeline is now processing {volume}TB/day. We need to optimize before we hit {limit}TB.",
            ),
            (
                1,
                "Have you considered switching from batch to micro-batch processing? Spark Structured Streaming could help.",
            ),
            (
                0,
                "We're evaluating it. The main concern is exactly-once semantics for our aggregation pipelines.",
            ),
            (
                1,
                "That's solvable with idempotent sinks. Happy to share our approach from the ML pipeline.",
            ),
            (0, "That would be really helpful. Can we set up a 30-min sync this week?"),
            (1, "Sure, how about {day} at {time}?"),
        ],
        [
            (
                0,
                "Model v{model_version} results: precision {precision}%, recall {recall}%, F1 {f1}%.",
            ),
            (0, "This is a {improvement}% improvement over the previous version."),
            (1, "What changed? The improvement is significant."),
            (
                0,
                "Better feature engineering on temporal features and expanded training set with Q{quarter} data.",
            ),
            (
                2,
                "What's the inference latency? We need to keep it under {latency_target}ms for real-time.",
            ),
            (
                0,
                "Currently at {current_latency}ms p99. The optimized inference runtime compensates for the larger model.",
            ),
            (1, "Ready for A/B testing in production?"),
            (
                0,
                "Need one more {n} weeks for bias testing and edge case validation. Then gradual rollout.",
            ),
        ],
    ],
    "ml-ops": [
        [
            (0, "ML model deployment pipeline proposal:"),
            (
                0,
                "1. Model registry in {registry}\n2. Automated validation tests ({validation_tests})\n3. Shadow deployment\n4. A/B test with {pct}% traffic\n5. Gradual rollout",
            ),
            (1, "How does this integrate with our existing {ci_cd} pipeline?"),
            (
                0,
                "It uses the same {ci_cd} pipeline. The model artifact is versioned in {storage} and referenced by hash.",
            ),
            (2, "For rollback, can we do instant model revert or do we need a full redeployment?"),
            (
                0,
                "Instant revert via feature flag. The model version is a runtime config, not a build-time dependency.",
            ),
            (1, "This looks solid. Let's implement it for {model_name} first, then generalize."),
        ],
    ],
    "security-incidents": [
        [
            (0, "Security incident report: {incident_type} detected from IP range {ip_range}."),
            (0, "Impact: {impact_count} records accessed. No passwords or payment data exposed."),
            (1, "How was the access achieved?"),
            (0, "Exploited a misconfigured {vulnerable_component} that allowed {exploit_type}."),
            (
                0,
                "Remediation:\n1. Blocked IP range at WAF\n2. Fixed {vulnerable_component}\n3. Reduced response fields\n4. Notified affected parties",
            ),
            (
                2,
                "Full postmortem scheduled for tomorrow. Please include recommendations for automated detection.",
            ),
            (0, "Will do. I'm also drafting an RFC for {proposed_rfc}."),
        ],
    ],
    "team-alpha": [
        [
            (0, "Team Alpha sprint retro: what went well, what to improve."),
            (1, "Went well: {positive_1}. Could improve: {improve_1}."),
            (2, "Went well: {positive_2}. Could improve: {improve_2}."),
            (3, "Went well: {positive_3}. Could improve: {improve_3}."),
            (0, "Action items:\n1. {action_1}\n2. {action_2}\n3. {action_3}"),
        ],
    ],
}


# ---------------------------------------------------------------------------
# Data generation
# ---------------------------------------------------------------------------


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
            fake_messages.append(msg)

        threads[(ch_id, thread_ts)] = fake_messages
        thread_count += 1

    return threads


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------


class Workspace:
    """Pre-generated fake workspace data with pagination support."""

    def __init__(
        self,
        params: WorkspaceParams | None = None,
        **kwargs: Any,
    ) -> None:
        if params is None and kwargs:
            params = WorkspaceParams(**kwargs)
        self.params = params or WorkspaceParams()
        rng = random.Random(self.params.seed)

        self.users = _generate_users(rng, self.params)
        self.channels = _generate_channels(rng, self.params)
        self.threads = _generate_threads(rng, self.params, self.channels, self.users)
        log.info(
            "workspace_generated",
            users=len(self.users),
            channels=len(self.channels),
            threads=len(self.threads),
            seed=self.params.seed,
        )

    def get_users_page(
        self, cursor: str | None, limit: int
    ) -> tuple[list[dict[str, Any]], str | None]:
        offset = _decode_cursor(cursor)
        page = self.users[offset : offset + limit]
        next_offset = offset + limit
        next_cursor = _encode_cursor(next_offset) if next_offset < len(self.users) else None
        return page, next_cursor

    def get_channels_page(
        self, cursor: str | None, limit: int, types: str | None
    ) -> tuple[list[dict[str, Any]], str | None]:
        filtered = self._filter_channels(types)
        offset = _decode_cursor(cursor)
        page = filtered[offset : offset + limit]
        next_offset = offset + limit
        next_cursor = _encode_cursor(next_offset) if next_offset < len(filtered) else None
        return page, next_cursor

    def _filter_channels(self, types: str | None) -> list[dict[str, Any]]:
        if not types:
            return self.channels
        wanted = {t.strip() for t in types.split(",")}
        result: list[dict[str, Any]] = []
        for ch in self.channels:
            ch_type = self._channel_type(ch)
            if ch_type in wanted:
                result.append(ch)
        return result

    @staticmethod
    def _channel_type(ch: dict[str, Any]) -> str:
        if ch.get("is_im"):
            return "im"
        if ch.get("is_mpim"):
            return "mpim"
        if ch.get("is_private"):
            return "private_channel"
        return "public_channel"

    def get_thread_messages(
        self,
        channel: str,
        thread_ts: str,
        oldest: str | None,
        cursor: str | None,
        limit: int,
    ) -> tuple[list[dict[str, Any]], bool, str | None]:
        all_messages = self.threads.get((channel, thread_ts), [])
        if oldest is not None:
            oldest_f = float(oldest)
            all_messages = [m for m in all_messages if float(m["ts"]) >= oldest_f]

        offset = _decode_cursor(cursor)
        page = all_messages[offset : offset + limit]
        next_offset = offset + limit
        has_more = next_offset < len(all_messages)
        next_cursor = _encode_cursor(next_offset) if has_more else None
        return page, has_more, next_cursor

    def thread_exists(self, channel: str, thread_ts: str) -> bool:
        return (channel, thread_ts) in self.threads

    def get_channel_history(
        self,
        channel: str,
        oldest: str | None,
        latest: str | None,
        cursor: str | None,
        limit: int,
    ) -> tuple[list[dict[str, Any]], bool, str | None]:
        """Return top-level messages (thread roots) for a channel.

        Each thread's first message is returned, simulating what
        conversations.history returns (standalone messages and thread parents).
        """
        roots: list[dict[str, Any]] = []
        for (ch_id, _ts), messages in self.threads.items():
            if ch_id != channel or not messages:
                continue
            root = dict(messages[0])
            reply_count = len(messages) - 1
            root["reply_count"] = reply_count
            if reply_count > 0:
                root["reply_users"] = list({m["user"] for m in messages[1:] if m.get("user")})
                root["latest_reply"] = messages[-1]["ts"]
            roots.append(root)

        roots.sort(key=lambda m: float(m["ts"]))

        if oldest is not None:
            oldest_f = float(oldest)
            roots = [m for m in roots if float(m["ts"]) >= oldest_f]
        if latest is not None:
            latest_f = float(latest)
            roots = [m for m in roots if float(m["ts"]) <= latest_f]

        offset = _decode_cursor(cursor)
        page = roots[offset : offset + limit]
        next_offset = offset + limit
        has_more = next_offset < len(roots)
        next_cursor = _encode_cursor(next_offset) if has_more else None
        return page, has_more, next_cursor

    def post_message(
        self,
        channel: str,
        text: str,
        user: str | None = None,
        thread_ts: str | None = None,
    ) -> dict[str, Any]:
        """Add a message to the workspace, either as a new thread or a reply.

        Returns the created message dict (matching the Slack ``chat.postMessage``
        response shape).
        """
        ts = f"{time.time():.6f}"
        if user is None:
            user = self.users[0]["id"] if self.users else "UPOST"

        msg: dict[str, Any] = {
            "type": "message",
            "user": user,
            "text": text,
            "ts": ts,
            "blocks": [],
            "files": [],
            "upload": False,
            "display_as_bot": False,
            "is_starred": False,
            "source_team": TEAM_ID,
            "user_team": TEAM_ID,
        }

        if thread_ts:
            key = (channel, thread_ts)
            if key not in self.threads:
                return {"ok": False, "error": "thread_not_found"}
            msg["thread_ts"] = thread_ts
            msg["parent_user_id"] = self.threads[key][0]["user"]
            self.threads[key].append(msg)
        else:
            msg["thread_ts"] = ts
            self.threads[(channel, ts)] = [msg]

        return {
            "ok": True,
            "ts": ts,
            "channel": channel,
            "message": msg,
        }


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class FakeSlackHandler(BaseHTTPRequestHandler):
    workspace: Workspace
    rate_limiter: RateLimiter | None = None

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        if self.rate_limiter is not None:
            allowed, retry_after = self.rate_limiter.check(path)
            if not allowed:
                self._send_json(
                    {"ok": False, "error": "ratelimited"},
                    429,
                    extra_headers={"Retry-After": str(retry_after)},
                )
                return

        routes = {
            "/api/conversations.replies": self._handle_conversations_replies,
            "/api/conversations.history": self._handle_conversations_history,
            "/api/users.list": self._handle_users_list,
            "/api/conversations.list": self._handle_conversations_list,
        }

        handler = routes.get(path)
        if handler is None:
            self._send_json({"ok": False, "error": "unknown_endpoint"}, 404)
            return
        handler(params)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode() if content_length else ""
        params: dict[str, str] = {}
        if body:
            for pair in body.split("&"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    from urllib.parse import unquote_plus

                    params[k] = unquote_plus(v)
        if not params:
            params = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        if self.rate_limiter is not None:
            allowed, retry_after = self.rate_limiter.check(path)
            if not allowed:
                self._send_json(
                    {"ok": False, "error": "ratelimited"},
                    429,
                    extra_headers={"Retry-After": str(retry_after)},
                )
                return

        routes = {
            "/api/chat.postMessage": self._handle_chat_post_message,
        }

        handler = routes.get(path)
        if handler is None:
            self._send_json({"ok": False, "error": "unknown_endpoint"}, 404)
            return
        handler(params)

    def _handle_conversations_replies(self, params: dict[str, str]) -> None:
        channel = params.get("channel", "")
        thread_ts = params.get("ts", "")
        limit = int(params.get("limit", "200"))
        oldest = params.get("oldest")
        cursor = params.get("cursor")

        if not self.workspace.thread_exists(channel, thread_ts):
            self._send_json({"ok": False, "error": "channel_not_found"}, 404)
            return

        messages, has_more, next_cursor = self.workspace.get_thread_messages(
            channel, thread_ts, oldest, cursor, limit
        )
        response: dict[str, Any] = {
            "ok": True,
            "messages": messages,
            "has_more": has_more,
        }
        if next_cursor is not None:
            response["response_metadata"] = {"next_cursor": next_cursor}
        else:
            response["response_metadata"] = {"next_cursor": ""}

        self._send_json(response)

    def _handle_conversations_history(self, params: dict[str, str]) -> None:
        channel = params.get("channel", "")
        limit = int(params.get("limit", "200"))
        oldest = params.get("oldest")
        latest = params.get("latest")
        cursor = params.get("cursor")

        messages, has_more, next_cursor = self.workspace.get_channel_history(
            channel, oldest, latest, cursor, limit
        )
        response: dict[str, Any] = {
            "ok": True,
            "messages": messages,
            "has_more": has_more,
        }
        if next_cursor is not None:
            response["response_metadata"] = {"next_cursor": next_cursor}
        else:
            response["response_metadata"] = {"next_cursor": ""}

        self._send_json(response)

    def _handle_users_list(self, params: dict[str, str]) -> None:
        limit = int(params.get("limit", "1000"))
        cursor = params.get("cursor")

        page, next_cursor = self.workspace.get_users_page(cursor, limit)
        response: dict[str, Any] = {
            "ok": True,
            "members": page,
        }
        if next_cursor is not None:
            response["response_metadata"] = {"next_cursor": next_cursor}
        else:
            response["response_metadata"] = {"next_cursor": ""}

        self._send_json(response)

    def _handle_conversations_list(self, params: dict[str, str]) -> None:
        limit = int(params.get("limit", "1000"))
        types = params.get("types")
        cursor = params.get("cursor")

        page, next_cursor = self.workspace.get_channels_page(cursor, limit, types)
        response: dict[str, Any] = {
            "ok": True,
            "channels": page,
        }
        if next_cursor is not None:
            response["response_metadata"] = {"next_cursor": next_cursor}
        else:
            response["response_metadata"] = {"next_cursor": ""}

        self._send_json(response)

    def _handle_chat_post_message(self, params: dict[str, str]) -> None:
        channel = params.get("channel", "")
        text = params.get("text", "")
        user = params.get("user") or params.get("as_user")
        thread_ts = params.get("thread_ts")

        if not channel:
            self._send_json({"ok": False, "error": "invalid_channel"}, 400)
            return
        if not text:
            self._send_json({"ok": False, "error": "no_text"}, 400)
            return

        result = self.workspace.post_message(
            channel=channel, text=text, user=user, thread_ts=thread_ts
        )
        if not result.get("ok"):
            self._send_json(result, 404)
            return
        self._send_json(result)

    def _send_json(
        self,
        data: dict[str, Any],
        status: int = 200,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        log.debug("http_request", message=format % args)


# ---------------------------------------------------------------------------
# Server runner
# ---------------------------------------------------------------------------


def run_server(
    host: str = "127.0.0.1",
    port: int = 8199,
    params: WorkspaceParams | None = None,
) -> HTTPServer:
    workspace = Workspace(params=params)
    FakeSlackHandler.workspace = workspace
    FakeSlackHandler.rate_limiter = RateLimiter() if workspace.params.rate_limits else None
    server = HTTPServer((host, port), FakeSlackHandler)
    log.info(
        "fake_slack_server_starting",
        host=host,
        port=port,
        seed=workspace.params.seed,
        users=len(workspace.users),
        channels=len(workspace.channels),
        threads=len(workspace.threads),
        rate_limits=workspace.params.rate_limits,
    )
    return server


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="slack-fake-server",
        description="Fake Slack API server with configurable, realistic workspace data.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8199, help="Port to listen on (default: 8199).")
    parser.add_argument(
        "--seed", type=int, default=42, help="Random seed for data generation (default: 42)."
    )
    parser.add_argument(
        "--num-users", type=int, default=20, help="Number of workspace members (default: 20)."
    )
    parser.add_argument(
        "--num-channels",
        type=int,
        default=13,
        help="Number of channels (default: 13).",
    )
    parser.add_argument(
        "--num-threads",
        type=int,
        default=30,
        help="Number of conversation threads (default: 30).",
    )
    parser.add_argument(
        "--messages-per-thread",
        default="3-12",
        help="Message count range per thread, e.g. '3-12' or '5' (default: 3-12).",
    )
    parser.add_argument(
        "--activity-ratio",
        type=float,
        default=0.6,
        help="Fraction of users who actively participate (default: 0.6).",
    )
    parser.add_argument(
        "--rate-limits",
        action="store_true",
        default=False,
        help="Enable Slack-compatible rate limiting (disabled by default).",
    )
    parser.add_argument(
        "--epoch-base",
        default=None,
        help="Base epoch timestamp for generated data. Accepts 'now', an ISO "
        "datetime (e.g. '2025-06-01'), or a unix timestamp. "
        "Default: 1704067200.0 (2024-01-01).",
    )

    args = parser.parse_args(argv)
    min_mpt, max_mpt = _parse_range_flag(args.messages_per_thread)
    epoch_base = _parse_epoch_base(args.epoch_base)

    params = WorkspaceParams(
        seed=args.seed,
        num_users=args.num_users,
        num_channels=args.num_channels,
        num_threads=args.num_threads,
        min_messages_per_thread=min_mpt,
        max_messages_per_thread=max_mpt,
        activity_ratio=args.activity_ratio,
        rate_limits=args.rate_limits,
        epoch_base=epoch_base,
    )

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )

    server = run_server(host=args.host, port=args.port, params=params)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("fake_slack_server_shutting_down")
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
