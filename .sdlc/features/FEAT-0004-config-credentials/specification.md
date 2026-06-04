---
title: "Configuration and Credential Management"
status: draft
---

# Specification: Configuration and Credential Management

## Overview

Credential resolution is handled by config.load_credentials(), which checks
environment variables first, then falls back to a config file parsed by
python-dotenv. Path defaults follow the XDG base directory specification.

## Architecture

```
config.py
  |
  +-- load_credentials()
  |     -> check SLACK_TOKEN / SLACK_COOKIE env vars
  |     -> fall back to config file via _load_config_file()
  |     -> validate xoxc- token has matching cookie
  |     -> return Credentials(token, cookie)
  |
  +-- default_db_path()
  |     -> cache_dir() / "threads.db"
  |     -> $XDG_CACHE_HOME/slack-cached/threads.db
  |     -> ~/.cache/slack-cached/threads.db
  |
  +-- config_dir()
        -> $XDG_CONFIG_HOME/slack-cached
        -> ~/.config/slack-cached
```

## Data Models

### Credentials

| Field | Type | Constraints | Description |
|---|---|---|---|
| token | str | not null | Slack API token (xoxb- or xoxc-) |
| cookie | str | nullable | Matching xoxd- d cookie, required for xoxc- tokens |

## API Contracts

No external API. Internal interface:

### load_credentials() -> Credentials

- Raises SystemExit if no token is found
- Raises SystemExit if xoxc- token lacks a matching cookie

### default_db_path() -> Path

- Returns the default SQLite database path based on XDG conventions

## Sequences

### Credential resolution

```
load_credentials()
  -> read SLACK_TOKEN from env
  -> read SLACK_COOKIE from env
  -> if token missing or cookie missing:
       -> _load_config_file() -> parse KEY=VALUE from config file
       -> fill in missing values from file
  -> if still no token: raise SystemExit with instructions
  -> if xoxc- and no cookie: raise SystemExit with xoxc-specific message
  -> return Credentials(token, cookie)
```

## Technical Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Config file format | KEY=VALUE (dotenv) | Simple, widely understood, no need for nested config |
| Config parsing | python-dotenv | Handles .env-style files robustly, including quoted values and comments |
| XDG paths | os.environ + Path.home() fallback | Standard on Linux; works on macOS with sensible defaults |
| Credential validation | Check xoxc- prefix requires cookie | Browser tokens need their d cookie; failing early gives a clear error |

## Risks and Unknowns

1. Config file permissions are not checked; tokens may be readable by other users
2. No keyring or encrypted credential storage is supported

## Out of Scope

- OAuth flow or token refresh
- Multiple workspace profiles
- Encrypted credential storage
