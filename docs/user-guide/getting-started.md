# Getting started

## Installation

Clone the repository and install dependencies with uv:

```bash
git clone https://github.com/TomzxCode/slack-cached.git
cd slack-cached
uv sync --dev
```

Python 3.13 or later is required.

## Slack credentials

slack-cached needs a Slack token to access the API. Two authentication methods are supported:

**Bot token (recommended)**

Set the `SLACK_TOKEN` environment variable to a bot token starting with `xoxb-`:

```bash
export SLACK_TOKEN=xoxb-your-bot-token
```

**Browser token**

Set both `SLACK_TOKEN` (starting with `xoxc-`) and `SLACK_COOKIE` (the `xoxd-` cookie from the same browser session):

```bash
export SLACK_TOKEN=xoxc-your-browser-token
export SLACK_COOKIE=xoxd-your-d-cookie
```

Alternatively, create a config file at `~/.config/slack-cached/config`:

```
SLACK_TOKEN=xoxb-your-bot-token
```

Environment variables take precedence over the config file.

## Quick start

Fetch a Slack thread by pasting its permalink URL:

```bash
slack-cached fetch "https://workspace.slack.com/archives/C01234/p1700000000123456"
```

Display the cached thread:

```bash
slack-cached show "https://workspace.slack.com/archives/C01234/p1700000000123456"
```

Fetch all workspace users and channels:

```bash
slack-cached fetch-users
slack-cached fetch-channels
```

## Cache location

The SQLite database is stored at `~/.cache/slack-cached/threads.db` by default, following the XDG base directory specification. Override with `--db`:

```bash
slack-cached --db /path/to/custom.db fetch <url>
```
