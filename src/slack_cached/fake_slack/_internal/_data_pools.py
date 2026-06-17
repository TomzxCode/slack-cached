"""Static data pools (names, roles, channel definitions, conversation templates)."""


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
