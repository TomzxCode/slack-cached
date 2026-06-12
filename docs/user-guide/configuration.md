# Configuration

## Credentials

Slack credentials can be provided through environment variables or a config file.

### Environment variables

```bash
export SLACK_TOKEN=xoxb-your-bot-token
```

For browser tokens, also set the cookie:

```bash
export SLACK_TOKEN=xoxc-your-browser-token
export SLACK_COOKIE=xoxd-your-d-cookie
```

### Config file

Create `~/.config/slack-cached/config` (or `$XDG_CONFIG_HOME/slack-cached/config`):

```
SLACK_TOKEN=xoxb-your-bot-token
SLACK_COOKIE=xoxd-your-d-cookie
```

Environment variables take precedence over the config file.

### Token types

| Token type | Prefix | Cookie required |
|---|---|---|
| Bot token | `xoxb-` | No |
| Browser token | `xoxc-` | Yes (`xoxd-`) |

## API base URL

Override the Slack API base URL to point at a fake server or custom endpoint:

```bash
# Via CLI flag
slack-cached --api-base-url http://localhost:8199/api fetch <url>

# Via environment variable
export SLACK_API_BASE_URL=http://localhost:8199/api

# Via config file
echo "SLACK_API_BASE_URL=http://localhost:8199/api" >> ~/.config/slack-cached/config
```

When `--api-base-url` points to a non-default server, credentials are optional.

## Database path

The default cache location follows XDG conventions:

| Platform | Default path |
|---|---|
| Linux | `~/.cache/slack-cached/threads.db` |
| Linux (XDG) | `$XDG_CACHE_HOME/slack-cached/threads.db` |

Override with `--db`:

```bash
slack-cached --db /path/to/custom.db fetch <url>
```

## Verbose mode

Enable debug logging with `-v` or `--verbose`:

```bash
slack-cached -v fetch <url>
```

This outputs SQL query timings, API request details, and other diagnostic information to stderr.
