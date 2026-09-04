/* slackx web UI.
 *
 * A single-file Vue 3 application (no build step, Vue loaded from CDN):
 * sidebar with channels and people, a Slack-like message list, a thread
 * panel, and a Ctrl+P palette for jumping between channels and messages.
 * The HTML template lives in index.html; this file holds state and logic.
 */
(function () {
  'use strict';

  if (!window.Vue) {
    return; // index.html already surfaced the CDN failure message.
  }

  // ------------------------------------------------------------- helpers

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function escapeRegExp(s) {
    return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  function initials(name) {
    var parts = String(name || '?').trim().split(/\s+/).filter(function (p) {
      return /^[a-z0-9]/i.test(p);
    });
    if (!parts.length) return '?';
    if (parts.length === 1) return parts[0].slice(0, 2);
    return parts[0][0] + parts[parts.length - 1][0];
  }

  function colorFor(id) {
    var hash = 0;
    var s = String(id || '');
    for (var i = 0; i < s.length; i++) hash = (hash * 31 + s.charCodeAt(i)) >>> 0;
    return 'hsl(' + (hash % 360) + ', 45%, 42%)';
  }

  function tsToDate(ts) {
    var f = parseFloat(ts);
    return isNaN(f) ? null : new Date(f * 1000);
  }

  function sameDay(a, b) {
    return a && b && a.getFullYear() === b.getFullYear() &&
      a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  }

  var MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
    'August', 'September', 'October', 'November', 'December'];

  function dayLabel(date) {
    if (!date) return '';
    var now = new Date();
    var yesterday = new Date(now.getTime() - 86400000);
    if (sameDay(date, now)) return 'Today';
    if (sameDay(date, yesterday)) return 'Yesterday';
    return MONTHS[date.getMonth()] + ' ' + date.getDate() + ', ' + date.getFullYear();
  }

  function fmtTime(date) {
    if (!date) return '';
    var h = date.getHours();
    var m = String(date.getMinutes()).padStart(2, '0');
    var ampm = h >= 12 ? 'PM' : 'AM';
    h = h % 12 || 12;
    return h + ':' + m + ' ' + ampm;
  }

  function fmtRelative(ts) {
    var date = tsToDate(ts);
    if (!date) return '';
    var diff = (Date.now() - date.getTime()) / 1000;
    if (diff < 90) return 'now';
    if (diff < 3600) return Math.round(diff / 60) + 'm';
    if (diff < 86400) return Math.round(diff / 3600) + 'h';
    if (diff < 86400 * 14) return Math.round(diff / 86400) + 'd';
    return dayLabel(date);
  }

  function groupByDay(messages) {
    var groups = [];
    var current = null;
    messages.forEach(function (m) {
      var date = tsToDate(m.ts);
      if (!current || !sameDay(current.date, date)) {
        current = { date: date, label: dayLabel(date), messages: [] };
        groups.push(current);
      }
      current.messages.push(m);
    });
    return groups;
  }

  // Highlight each query term (prefix match) inside already-escaped text.
  // Returns HTML using <mark>; input must be raw text.
  function highlightTerms(rawText, query) {
    var safe = escapeHtml(rawText);
    String(query || '').trim().split(/\s+/).forEach(function (term) {
      if (!term) return;
      var re = new RegExp('(' + escapeRegExp(escapeHtml(term)) + '\\w*)', 'gi');
      safe = safe.replace(re, '\u0001$1\u0002');
    });
    return safe.split('\u0001').join('<mark>').split('\u0002').join('</mark>');
  }

  // The server snippet wraps matches in [ ]; convert to <mark>.
  function markedSnippet(snippet) {
    return escapeHtml(snippet || '')
      .replace(/\[/g, '<mark>')
      .replace(/\]/g, '</mark>');
  }

  // Render Slack mrkdwn into safe HTML. ``text`` is raw message text;
  // ``ctx`` supplies {userNames, channelsById} for mention resolution.
  function renderMessageHtml(msg, ctx) {
    var text = msg.text;
    if (!text) return '';
    var codes = [];
    var stash = function (html) {
      codes.push(html);
      return '\u0000' + (codes.length - 1) + '\u0000';
    };

    var s = escapeHtml(text);

    // Fenced and inline code first, protected from the other rules.
    s = s.replace(/```([\s\S]*?)```/g, function (_, code) {
      return stash('<pre><code>' + code.replace(/^\n+|\n+$/g, '') + '</code></pre>');
    });
    s = s.replace(/`([^`\n]+)`/g, function (_, code) {
      return stash('<code>' + code + '</code>');
    });

    // Links: <url|label> then bare <url>.
    s = s.replace(/&lt;(https?:\/\/[^|&\s]+)\|([^&]+?)&gt;/g, function (_, url, label) {
      return '<a href="' + url + '" target="_blank" rel="noopener noreferrer">' + label + '</a>';
    });
    s = s.replace(/&lt;(https?:\/\/[^|&\s]+)&gt;/g, function (_, url) {
      return '<a href="' + url + '" target="_blank" rel="noopener noreferrer">' + url + '</a>';
    });

    // User mentions <@U123|label>.
    s = s.replace(/&lt;@(U[A-Z0-9]+)(?:\|([^&]*))?&gt;/g, function (_, uid, label) {
      var name = ctx.userNames[uid] || label || uid;
      return '<span class="mention">@' + escapeHtml(name) + '</span>';
    });

    // Channel mentions <#C123|name>.
    s = s.replace(/&lt;#(C[A-Z0-9]+)(?:\|([^&]*))?&gt;/g, function (_, cid, label) {
      var chan = ctx.channelsById[cid];
      var name = label || (chan && chan.name) || cid;
      return '<span class="mention channel-mention" data-channel="' + cid + '">#' +
        escapeHtml(name) + '</span>';
    });

    // Special mentions <!here> <!channel> <!everyone> and <!date^...|label>.
    s = s.replace(/&lt;!(here|channel|everyone)&gt;/g, function (_, w) {
      return '<span class="mention">@' + w + '</span>';
    });
    s = s.replace(/&lt;!date\^[^&]*\|([^&]+)&gt;/g, '$1');

    // Styling.
    s = s.replace(/\*([^*\n]+)\*/g, '<strong>$1</strong>');
    s = s.replace(/(^|[\s(])_([^_\n]+)_(?=$|[\s).,!?;:])/g, '$1<em>$2</em>');
    s = s.replace(/(^|[\s(])~([^~\n]+)~(?=$|[\s).,!?;:])/g, '$1<del>$2</del>');

    // Restore protected code segments.
    s = s.replace(/\u0000(\d+)\u0000/g, function (_, i) { return codes[+i]; });
    return s;
  }

  // Themes shipped by the daisyUI CDN stylesheet (themes.css), in menu order.
  // "System" resolves to light/dark via prefers-color-scheme.
  var THEMES = [
    'system', 'light', 'dark',
    'cupcake', 'bumblebee', 'emerald', 'corporate',
    'synthwave', 'retro', 'cyberpunk', 'valentine',
    'halloween', 'garden', 'forest', 'aqua',
    'lofi', 'pastel', 'dream', 'wireframe',
    'black', 'luxury', 'dracula', 'cmyk',
    'autumn', 'business', 'acid', 'lemonade',
    'night', 'coffee', 'winter', 'dim',
    'nord', 'sunset', 'caramellatte', 'abyss', 'silk', 'fantasy',
  ];

  // ------------------------------------------------------------- app

  var app = Vue.createApp({
    data: function () {
      return {
        booted: false,
        summary: null,
        users: [],
        channels: [],
        channelsById: {},
        userNames: {},

        view: 'home',            // 'home' | 'channel'
        channelId: null,
        channel: null,           // {id, name, is_private} of the open channel
        messages: [],            // thread roots, newest first
        hasMore: false,
        loadingMessages: false,
        loadingOlder: false,

        thread: null,            // {channel_id, thread_ts, messages, loading}
        highlightTs: null,

        refreshing: null,        // label of the refresh in progress
        toasts: [],
        toastSeq: 0,

        paletteOpen: false,
        paletteQuery: '',
        paletteItems: [],
        paletteActive: 0,
        paletteMsgsLoading: false,
        paletteTimer: null,

        profile: null,

        theme: 'dark',
        themes: THEMES,
      };
    },

    computed: {
      messageGroups: function () {
        // The API returns roots newest-first; display oldest at the top
        // like Slack, so reverse before grouping by day.
        return groupByDay(this.messages.slice().reverse());
      },

      threadGroups: function () {
        // Thread messages arrive chronologically already.
        return this.thread ? groupByDay(this.thread.messages) : [];
      },

      recentChannels: function () {
        return this.channels
          .filter(function (c) { return c.message_count > 0; })
          .sort(function (a, b) { return (b.latest_ts || 0) - (a.latest_ts || 0); })
          .slice(0, 8);
      },
    },

    methods: {
      // Expose helpers used by the in-DOM template.
      initials: initials,
      fmtRelative: fmtRelative,
      fmtTime: fmtTime,
      tsToDate: tsToDate,
      highlightTerms: highlightTerms,

      avatarStyle: function (id) {
        return { background: colorFor(id) };
      },

      msgCtx: function () {
        return { userNames: this.userNames, channelsById: this.channelsById };
      },

      groupTitle: function (type) {
        return type === 'channel' ? 'Channels'
          : type === 'person' ? 'People' : 'Messages';
      },

      markedSnippet: function (hit) {
        return hit.snippet ? markedSnippet(hit.snippet)
          : highlightTerms(hit.text || '', this.paletteQuery);
      },

      // -------------------------------------------------- api + toasts

      toast: function (text, kind) {
        var id = ++this.toastSeq;
        this.toasts.push({ id: id, text: text, kind: kind || '' });
        var self = this;
        setTimeout(function () {
          self.toasts = self.toasts.filter(function (t) { return t.id !== id; });
        }, 6000);
      },

      _api: function (path, opts) {
        return fetch(path, opts).then(function (res) {
          return res.json().catch(function () { return {}; }).then(function (body) {
            if (!res.ok) {
              var detail = (body && body.detail) || res.statusText;
              throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
            }
            return body;
          });
        });
      },

      post: function (path) { return this._api(path, { method: 'POST' }); },

      // -------------------------------------------------- boot

      boot: function () {
        var self = this;
        Promise.all([
          this._api('/api/summary'),
          this._api('/api/users'),
          this._api('/api/channels'),
        ]).then(function (results) {
          self.summary = results[0];
          self.users = results[1].users;
          self.channels = results[2].channels;
          var byId = {};
          var names = {};
          self.channels.forEach(function (c) { byId[c.id] = c; });
          self.users.forEach(function (u) { names[u.id] = u.display_name; });
          self.channelsById = byId;
          self.userNames = names;
          self.booted = true;
        }).catch(function (err) {
          self.toast('Failed to load cache: ' + err.message, 'error');
          self.booted = true;
        });
      },

      // -------------------------------------------------- channel view

      openChannel: function (channelId, opts) {
        opts = opts || {};
        var self = this;
        this.view = 'channel';
        this.channelId = channelId;
        this.channel = this.channelsById[channelId] ||
          { id: channelId, name: null, is_private: null };
        this.messages = [];
        this.hasMore = false;
        this.loadingMessages = true;
        this.closeThread();
        return this._api('/api/channels/' + encodeURIComponent(channelId) + '/messages')
          .then(function (body) {
            if (body.channel && body.channel.name) {
              // Search hits can reach channels missing from the sidebar cache.
              self.channelsById[channelId] = body.channel;
            }
            self.channel = body.channel;
            self.messages = body.messages;
            self.hasMore = body.has_more;
            self.loadingMessages = false;
            if (opts.highlight) self.scrollToMessage(opts.highlight);
          })
          .catch(function (err) {
            self.loadingMessages = false;
            self.toast('Failed to load channel: ' + err.message, 'error');
          });
      },

      loadOlder: function () {
        if (!this.messages.length || this.loadingOlder) return;
        var self = this;
        var scroller = this.$refs.msgScroll;
        var beforeHeight = scroller ? scroller.scrollHeight : 0;
        this.loadingOlder = true;
        var oldest = this.messages[this.messages.length - 1].ts;
        this._api('/api/channels/' + encodeURIComponent(this.channelId) +
          '/messages?before=' + encodeURIComponent(oldest))
          .then(function (body) {
            self.messages = self.messages.concat(body.messages);
            self.hasMore = body.has_more;
            self.loadingOlder = false;
            Vue.nextTick(function () {
              if (scroller) scroller.scrollTop = scroller.scrollHeight - beforeHeight;
            });
          })
          .catch(function (err) {
            self.loadingOlder = false;
            self.toast('Failed to load older messages: ' + err.message, 'error');
          });
      },

      // -------------------------------------------------- thread panel

      openThread: function (threadTs, highlightTs) {
        var self = this;
        this.thread = {
          channel_id: this.channelId,
          thread_ts: threadTs,
          messages: [],
          loading: true,
        };
        return this._api('/api/channels/' + encodeURIComponent(this.channelId) +
          '/threads/' + encodeURIComponent(threadTs))
          .then(function (body) {
            if (!self.thread || self.thread.thread_ts !== threadTs) return;
            self.thread.messages = body.messages;
            self.thread.loading = false;
            if (highlightTs) {
              Vue.nextTick(function () { self.scrollToMessage(highlightTs, 'threadScroll'); });
            }
          })
          .catch(function (err) {
            if (self.thread && self.thread.thread_ts === threadTs) {
              self.thread.loading = false;
            }
            self.toast('Failed to load thread: ' + err.message, 'error');
          });
      },

      closeThread: function () { this.thread = null; },

      openRootThread: function (msg) {
        if (msg.reply_count > 0) this.openThread(msg.ts);
      },

      // -------------------------------------------------- refresh (live)

      refresh: function (label, path, after) {
        if (this.refreshing) return;
        var self = this;
        this.refreshing = label;
        this.post(path).then(function (body) {
          self.refreshing = null;
          self.toast(label + ' complete' + self.refreshSummary(body), 'ok');
          if (after) after(body);
        }).catch(function (err) {
          self.refreshing = null;
          self.toast(label + ' failed: ' + err.message, 'error');
        });
      },

      refreshSummary: function (body) {
        var s = body && (body.channel || body.thread || body.users || body.channels);
        if (s && s.fetched_messages !== undefined) {
          return ' (' + s.fetched_messages + ' new, ' + s.total_messages + ' total)';
        }
        if (s && s.processed !== undefined) {
          return ' (' + s.added + ' new, ' + s.total + ' total)';
        }
        return '';
      },

      refreshChannel: function () {
        var self = this;
        var id = this.channelId;
        this.refresh('Channel refresh',
          '/api/channels/' + encodeURIComponent(id) + '/refresh',
          function () { self.openChannel(id); });
      },

      refreshThread: function () {
        var self = this;
        if (!this.thread) return;
        var ts = this.thread.thread_ts;
        this.refresh('Thread refresh',
          '/api/channels/' + encodeURIComponent(this.channelId) +
          '/threads/' + encodeURIComponent(ts) + '/refresh',
          function () { self.openThread(ts); });
      },

      refreshUsers: function () {
        this.refresh('User refresh', '/api/users/refresh', this.boot.bind(this));
      },

      refreshChannels: function () {
        this.refresh('Channel refresh', '/api/channels/refresh', this.boot.bind(this));
      },

      // -------------------------------------------------- palette

      openPalette: function () {
        this.closeProfile();
        this.paletteOpen = true;
        this.paletteQuery = '';
        this.paletteItems = this.localMatches('');
        this.paletteActive = 0;
        var self = this;
        Vue.nextTick(function () {
          if (self.$refs.paletteInput) self.$refs.paletteInput.focus();
        });
      },

      closePalette: function () {
        this.paletteOpen = false;
        if (this.paletteTimer) clearTimeout(this.paletteTimer);
      },

      localMatches: function (q) {
        var needle = q.trim().toLowerCase();
        var channels = this.channels.filter(function (c) {
          if (!needle) return true;
          return (c.name || '').toLowerCase().indexOf(needle) !== -1 ||
            c.id.toLowerCase().indexOf(needle) !== -1;
        }).sort(function (a, b) {
          var aStarts = (a.name || '').toLowerCase().startsWith(needle) ? 1 : 0;
          var bStarts = (b.name || '').toLowerCase().startsWith(needle) ? 1 : 0;
          return (bStarts - aStarts) || (b.message_count - a.message_count);
        }).slice(0, 8).map(function (c) {
          return {
            key: 'c-' + c.id,
            type: 'channel',
            id: c.id,
            name: c.name || c.id,
            is_private: c.is_private,
            message_count: c.message_count,
          };
        });

        var people = needle ? this.users.filter(function (u) {
          return u.display_name.toLowerCase().indexOf(needle) !== -1 ||
            (u.name || '').toLowerCase().indexOf(needle) !== -1;
        }).slice(0, 5).map(function (u) {
          return { key: 'u-' + u.id, type: 'person', user: u };
        }) : [];

        return channels.concat(people);
      },

      onPaletteInput: function () {
        var self = this;
        var q = this.paletteQuery;
        if (this.paletteTimer) clearTimeout(this.paletteTimer);
        this.paletteTimer = setTimeout(function () { self.searchPaletteMessages(q); }, 180);
        this.paletteItems = this.localMatches(q);
        this.paletteActive = 0;
      },

      searchPaletteMessages: function (q) {
        var trimmed = q.trim();
        if (trimmed.length < 2 || q !== this.paletteQuery) return;
        var self = this;
        this.paletteMsgsLoading = true;
        this._api('/api/search?q=' + encodeURIComponent(trimmed) + '&limit=12')
          .then(function (body) {
            self.paletteMsgsLoading = false;
            if (self.paletteQuery !== q) return;
            var msgs = body.hits.map(function (h) {
              return { key: 'm-' + h.channel + '-' + h.ts, type: 'message', hit: h };
            });
            self.paletteItems = self.localMatches(q).concat(msgs);
            if (self.paletteActive >= self.paletteItems.length) self.paletteActive = 0;
          })
          .catch(function () { self.paletteMsgsLoading = false; });
      },

      onPaletteKey: function (e) {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          this.paletteMove(1);
        } else if (e.key === 'ArrowUp') {
          e.preventDefault();
          this.paletteMove(-1);
        } else if (e.key === 'Enter') {
          e.preventDefault();
          this.paletteChoose();
        } else if (e.key === 'Escape') {
          this.closePalette();
        }
      },

      paletteMove: function (delta) {
        var n = this.paletteItems.length;
        if (!n) return;
        this.paletteActive = (this.paletteActive + delta + n) % n;
        var el = this.$refs.paletteResults;
        var node = el && el.querySelector('.palette-item.active');
        if (node) node.scrollIntoView({ block: 'nearest' });
      },

      paletteChoose: function () {
        var item = this.paletteItems[this.paletteActive];
        if (item) this.chooseItem(item);
      },

      chooseItem: function (item) {
        this.closePalette();
        if (item.type === 'channel') {
          this.openChannel(item.id);
        } else if (item.type === 'person') {
          this.showProfile(item.user);
        } else if (item.type === 'message') {
          this.jumpToMessage(item.hit);
        }
      },

      jumpToMessage: function (hit) {
        var self = this;
        this.openChannel(hit.channel).then(function () {
          if (hit.ts === hit.thread_ts) {
            self.scrollToMessage(hit.ts);
          } else {
            self.openThread(hit.thread_ts, hit.ts);
          }
        });
      },

      scrollToMessage: function (ts, scrollerRef) {
        var self = this;
        this.highlightTs = ts;
        Vue.nextTick(function () {
          var el = document.getElementById('m-' + String(ts).replace('.', '-'));
          var scroller = self.$refs[scrollerRef || 'msgScroll'];
          if (el && scroller) {
            // offsetTop is unreliable here (positioned ancestors); use rects.
            var top = el.getBoundingClientRect().top -
              scroller.getBoundingClientRect().top + scroller.scrollTop;
            scroller.scrollTop = top - scroller.clientHeight / 3;
          }
          setTimeout(function () { self.highlightTs = null; }, 2500);
        });
      },

      // -------------------------------------------------- profile

      showProfile: function (user) { this.profile = user; },

      closeProfile: function () { this.profile = null; },

      // -------------------------------------------------- theme

      applyTheme: function () {
        // Resolve the active data-theme; "System" follows the OS setting.
        var theme = this.theme;
        if (theme === 'system') {
          theme = window.matchMedia('(prefers-color-scheme: dark)').matches
            ? 'dark' : 'light';
        }
        document.documentElement.setAttribute('data-theme', theme);
      },

      setTheme: function (theme) {
        this.theme = theme;
        this.applyTheme();
        try {
          localStorage.setItem('slackx-theme', theme);
        } catch (e) { /* private mode etc.; theme still applies for the session */ }
      },

      chooseTheme: function (theme, evt) {
        this.setTheme(theme);
        // Close the dropdown: it stays open while any descendant holds focus
        // (the menu ul is tabindex=0, so it can retain focus after a click).
        var dropdown = evt.currentTarget.closest('.dropdown');
        if (dropdown && dropdown.contains(document.activeElement)) {
          document.activeElement.blur();
        }
      },

      // -------------------------------------------------- global

      globalKey: function (e) {
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'p') {
          e.preventDefault();
          if (this.paletteOpen) this.closePalette();
          else this.openPalette();
        } else if (e.key === 'Escape' && this.profile) {
          this.closeProfile();
        }
      },

      onMainClick: function (e) {
        var t = e.target.closest('[data-channel]');
        if (t) this.openChannel(t.getAttribute('data-channel'));
      },
    },

    mounted: function () {
      document.addEventListener('keydown', this.globalKey);
      // Follow OS light/dark changes while "System" is selected.
      if (window.matchMedia) {
        var mq = window.matchMedia('(prefers-color-scheme: dark)');
        var self = this;
        this._onSystemThemeChange = function () {
          if (self.theme === 'system') self.applyTheme();
        };
        if (mq.addEventListener) {
          mq.addEventListener('change', this._onSystemThemeChange);
        } else if (mq.addListener) {
          mq.addListener(this._onSystemThemeChange); // older browsers
        }
      }
      var stored = null;
      try {
        stored = localStorage.getItem('slackx-theme');
      } catch (e) { /* ignore */ }
      this.setTheme(THEMES.indexOf(stored) !== -1 ? stored : 'system');
      this.boot();
    },

    beforeUnmount: function () {
      document.removeEventListener('keydown', this.globalKey);
      if (window.matchMedia) {
        var mq = window.matchMedia('(prefers-color-scheme: dark)');
        if (mq.removeEventListener) {
          mq.removeEventListener('change', this._onSystemThemeChange);
        } else if (mq.removeListener) {
          mq.removeListener(this._onSystemThemeChange);
        }
      }
    },
  });

  // One message (avatar, author, time, mrkdwn text, optional thread bar).
  // ``ctx`` carries the user/channel maps needed for mention resolution.
  app.component('message-row', {
    props: {
      msg: { type: Object, required: true },
      ctx: { type: Object, required: true },
      highlight: { type: Boolean, default: false },
      showThreadBar: { type: Boolean, default: false },
    },
    emits: ['open-thread'],
    computed: {
      html: function () { return renderMessageHtml(this.msg, this.ctx); },
      author: function () { return this.msg.user_name || this.msg.user || 'unknown'; },
      time: function () { return fmtTime(tsToDate(this.msg.ts)); },
    },
    methods: {
      initials: initials,
      rel: fmtRelative,
      avatarStyle: function () { return { background: colorFor(this.msg.user) }; },
    },
    template: [
      '<div class="msg flex gap-2.5 px-5 py-1" :id="\'m-\' + msg.ts.replace(\'.\', \'-\')" :class="{highlight: highlight}">',
      '  <div class="avatar placeholder">',
      '    <div class="w-9 rounded-md" :style="avatarStyle()"><span class="text-[11px] font-bold uppercase">{{ initials(author) }}</span></div>',
      '  </div>',
      '  <div class="min-w-0 flex-1">',
      '    <div class="flex items-baseline gap-2">',
      '      <span class="font-black text-base-content">{{ author }}</span>',
      '      <span class="badge badge-ghost badge-xs" v-if="msg.payload && msg.payload.bot_id">APP</span>',
      '      <span class="text-xs opacity-50">{{ time }}</span>',
      '    </div>',
      '    <div class="text whitespace-pre-wrap break-words" v-html="html"></div>',
      '    <button v-if="showThreadBar && msg.reply_count > 0" class="thread-bar btn btn-ghost btn-xs -ml-1 mt-0.5 px-1 text-primary"',
      '            @click.stop="$emit(\'open-thread\', msg)">',
      '      &#128172; {{ msg.reply_count === 1 ? \'1 reply\' : msg.reply_count + \' replies\' }}',
      '      <template v-if="msg.latest_reply_ts">&middot; last reply {{ rel(msg.latest_reply_ts) }}</template>',
      '    </button>',
      '  </div>',
      '</div>',
    ].join(''),
  });

  app.mount('#app');
})();
