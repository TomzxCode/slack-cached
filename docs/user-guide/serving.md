# Serving the web UI

## Basic usage

Browse the local cache in a browser:

```bash
slackx serve
```

Then open <http://127.0.0.1:8280>.

| Flag | Default | Description |
|---|---|---|
| `--host` | `127.0.0.1` | Interface to bind to |
| `--port` | `8280` | Port to bind to |

The server only binds to localhost by default, so the cache is not exposed to
the network.

## What the UI shows

The interface mimics Slack's layout over the cached database:

- A sidebar listing cached channels (with message counts) and people.
- A message pane per channel with day dividers, Slack mrkdwn rendering
  (bold, italic, code, links, `@user` and `#channel` mentions), and
  "Load older messages" paging.
- A thread panel: click the reply count under a message to read the full
  thread alongside the channel.
- A home view with cache counters and recently active channels.

## Themes

The palette icon in the sidebar header opens a theme menu with a **System**
option (follows the OS light/dark setting, updating live when it changes)
plus every [daisyUI](https://daisyui.com) theme (light, dark, cupcake,
dracula, nord and friends). The choice is remembered per browser via
localStorage; the UI defaults to System.

## Jumping with Ctrl+P

Press `Ctrl+P` (or `Cmd+P`) anywhere to open the jump palette:

- Type to filter channels and people instantly.
- Keep typing (2+ characters) to also full-text search every cached
  conversation.
- `ArrowUp`/`ArrowDown` to navigate, `Enter` to open, `Esc` to close.
- Choosing a message jumps to its channel, opens its thread, and highlights
  the matched message.

Search uses a SQLite FTS5 index over cached message text, kept in sync
automatically as messages are cached. The index is built once on first
`serve` startup for databases created by older versions.

## Refreshing from the browser

Refresh buttons in the sidebar and channel header trigger live Slack fetches
through the same client and credential resolution as the CLI:

- The `⟳` next to **Channels**/**People** re-fetches the workspace lists.
- **Refresh from Slack** in a channel header fetches that channel's messages
  (including full threads).
- The `⟳` in a thread header refreshes just that thread.

Without configured credentials (`SLACK_TOKEN`/`SLACK_COOKIE` or the config
file, see [Configuration](configuration.md)) the refresh buttons report an
error instead of fetching. Browsing the existing cache works without any
credentials.

## Notes

- The UI is served from static assets bundled in the package; Vue is loaded
  from a CDN, so the browser needs internet access on first load.
- All data is served from the local SQLite cache; nothing is sent anywhere.
- The JSON API under `/api/*` (summary, users, channels, messages, threads,
  search) can be used directly, e.g. for scripting.
