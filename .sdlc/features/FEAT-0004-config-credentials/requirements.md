---
title: "Configuration and Credential Management"
status: draft
---

# Requirements: Configuration and Credential Management

## Overview

Provides credential resolution for authenticating with the Slack API, path
defaults for the cache database, and configurable API base URL for testing.
Credentials can come from environment variables or a config file, with
environment variables taking precedence.

## Stakeholders

| Stakeholder | Interest |
|---|---|
| CLI user | Needs a convenient and secure way to provide Slack credentials |
| Developer | Needs configurable paths for testing and development |

## Functional Requirements

Order rows by priority: Must first, then Should, then May.

| ID | Priority | Requirement |
|---|---|---|
| FR-01 | Must | The system shall load Slack credentials from SLACK_TOKEN and SLACK_COOKIE environment variables |
| FR-02 | Must | The system shall load credentials from a config file at $XDG_CONFIG_HOME/slack-cached/config (falling back to ~/.config/slack-cached/config) |
| FR-03 | Must | Environment variables shall take precedence over the config file |
| FR-04 | Must | The system shall exit with a clear error message when no token is found |
| FR-05 | Must | The system shall validate that xoxc- tokens are accompanied by a cookie and exit with an error if missing |
| FR-06 | Must | The system shall default the cache database path to $XDG_CACHE_HOME/slack-cached/threads.db (falling back to ~/.cache/slack-cached/threads.db) |
| FR-07 | Must | The system shall allow overriding the cache database path via --db |
| FR-08 | Must | The system shall support a configurable API base URL via SLACK_API_BASE_URL env var, config file, or --api-base-url CLI flag |
| FR-09 | Should | The system shall allow credential loading to be optional (require=False) when a non-default API base URL is used, enabling use with the fake server without real credentials |

## Non-Functional Requirements

Order rows by priority: Must first, then Should, then May.

| ID | Priority | Category | Requirement |
|---|---|---|---|
| NFR-01 | Must | Security | Tokens and cookies shall never be logged or printed to stdout |
| NFR-02 | Should | Usability | Error messages shall explain both authentication methods (env vars and config file) |

## Constraints

- Config file uses a simple KEY=VALUE format (same as .env), parsed by python-dotenv
- XDG base directory specification is followed for both config and cache paths

## Acceptance Criteria

- [ ] FR-01: Setting SLACK_TOKEN and SLACK_COOKIE env vars allows successful authentication
- [ ] FR-02: A config file with KEY=VALUE lines is read when env vars are absent
- [ ] FR-03: Env vars override config file values when both are present
- [ ] FR-04: Missing token raises SystemExit with instructions for both methods
- [ ] FR-05: xoxc- token without SLACK_COOKIE raises SystemExit with a specific message
- [ ] FR-06: default_db_path() returns the correct XDG path

## Open Questions

1. Should the config file support multiple workspace profiles?
2. Should credentials be validated at load time (e.g. by calling auth.test)?
