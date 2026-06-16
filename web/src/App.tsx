import { useEffect, useState, useCallback } from "react";
import { api } from "./api";
import type { Query, Run, Settings, CacheChannel, Health } from "./api";
import { QueryEditor } from "./QueryEditor";
import { RunCard } from "./RunCard";
import { SettingsPanel } from "./SettingsPanel";

type Tab = "runs" | "edit" | "settings";

export default function App() {
  const [queries, setQueries] = useState<Query[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [tab, setTab] = useState<Tab>("runs");
  const [health, setHealth] = useState<Health | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [channels, setChannels] = useState<CacheChannel[]>([]);
  const [toast, setToast] = useState<{ text: string; kind: "error" | "success" } | null>(null);
  const [loadingRuns, setLoadingRuns] = useState(false);

  const showToast = useCallback((text: string, kind: "error" | "success" = "success") => {
    setToast({ text, kind });
    window.setTimeout(() => setToast(null), 2500);
  }, []);

  const refreshQueries = useCallback(async () => {
    try {
      const list = await api.listQueries();
      setQueries(list);
      if (!selectedId && list.length > 0) setSelectedId(list[0].id);
      if (selectedId && !list.find((q) => q.id === selectedId)) {
        setSelectedId(list.length > 0 ? list[0].id : null);
      }
    } catch (e) {
      showToast((e as Error).message, "error");
    }
  }, [selectedId, showToast]);

  const refreshHealth = useCallback(async () => {
    try {
      setHealth(await api.getHealth());
    } catch {
      setHealth(null);
    }
  }, []);

  const refreshSettings = useCallback(async () => {
    try {
      setSettings(await api.getSettings());
    } catch (e) {
      showToast((e as Error).message, "error");
    }
  }, [showToast]);

  const refreshChannels = useCallback(async () => {
    try {
      setChannels(await api.listCacheChannels());
    } catch {
      setChannels([]);
    }
  }, []);

  const refreshRuns = useCallback(
    async (queryId: string | null) => {
      setLoadingRuns(true);
      try {
        const list = await api.listRuns(queryId ?? undefined, 100, 0);
        setRuns(list);
      } catch (e) {
        showToast((e as Error).message, "error");
      } finally {
        setLoadingRuns(false);
      }
    },
    [showToast],
  );

  useEffect(() => {
    refreshQueries();
    refreshHealth();
    refreshSettings();
    refreshChannels();
    const id = window.setInterval(() => {
      refreshQueries();
      refreshHealth();
      refreshRuns(selectedId);
    }, 5000);
    return () => window.clearInterval(id);
  }, [refreshQueries, refreshHealth, refreshSettings, refreshChannels, refreshRuns, selectedId]);

  useEffect(() => {
    refreshRuns(selectedId);
  }, [selectedId, refreshRuns]);

  const selected = queries.find((q) => q.id === selectedId) || null;

  async function handleSave(query: Omit<Query, "id" | "created_at" | "updated_at">) {
    if (!selected) return;
    try {
      await api.updateQuery(selected.id, query);
      showToast("Saved");
      await refreshQueries();
      setTab("runs");
    } catch (e) {
      showToast((e as Error).message, "error");
    }
  }

  async function handleCreate() {
    if (!settings) return;
    const body: Omit<Query, "id" | "created_at" | "updated_at"> = {
      name: "New query",
      source_kind: "channels",
      source_config: { channel_ids: [] },
      prompt: "Summarize this Slack thread in 3-5 bullet points.\n\nThread:\n{{thread}}",
      interval: "5m",
      lookback: "1h",
      dedup: "new_messages",
      full_threads: true,
      model: settings.default_model || "gpt-4o-mini",
      enabled: false,
    };
    try {
      const created = await api.createQuery(body);
      await refreshQueries();
      setSelectedId(created.id);
      setTab("edit");
      showToast("Created");
    } catch (e) {
      showToast((e as Error).message, "error");
    }
  }

  async function handleDelete() {
    if (!selected) return;
    if (!confirm(`Delete query "${selected.name}"? This also deletes its runs.`)) return;
    try {
      await api.deleteQuery(selected.id);
      setSelectedId(null);
      await refreshQueries();
      showToast("Deleted");
    } catch (e) {
      showToast((e as Error).message, "error");
    }
  }

  async function handleToggleEnabled() {
    if (!selected) return;
    try {
      await api.updateQuery(selected.id, { ...selected });
      // The above sends the existing shape; the toggle below mutates enabled.
    } catch (e) {
      showToast((e as Error).message, "error");
    }
  }

  async function handleTriggerRun() {
    if (!selected) return;
    try {
      await api.triggerQuery(selected.id);
      showToast("Scheduled");
    } catch (e) {
      showToast((e as Error).message, "error");
    }
  }

  return (
    <div className="app">
      <header className="header">
        <h1>Slack Watcher</h1>
        <span className="health">
          {health ? (
            <>
              <span className={`dot ${health.scheduler_running ? "on" : "off"}`} />
              {health.scheduler_running ? "scheduler running" : "scheduler idle"}
            </>
          ) : (
            "no backend"
          )}
        </span>
        <div className="spacer" />
        <span className="health">
          {queries.length} {queries.length === 1 ? "query" : "queries"}
        </span>
      </header>

      <aside className="sidebar">
        <div className="sidebar-head">
          <h2>Queries</h2>
          <button className="small primary" onClick={handleCreate}>
            + New
          </button>
        </div>
        <div className="sidebar-list">
          {queries.length === 0 && (
            <div className="empty">
              No queries yet.
              <br />
              Click <strong>+ New</strong> to create one.
            </div>
          )}
          {queries.map((q) => (
            <div
              key={q.id}
              className={`query-item ${q.id === selectedId ? "selected" : ""}`}
              onClick={() => {
                setSelectedId(q.id);
                setTab("runs");
              }}
            >
              <div className="name">
                <span className={`dot ${q.enabled ? "on" : "off"}`} />
                {q.name}
              </div>
              <div className="meta">
                <span className={`tag ${q.source_kind}`}>{q.source_kind}</span>
                <span>{q.interval}</span>
                <span>{q.model}</span>
              </div>
            </div>
          ))}
        </div>
      </aside>

      <main className="main">
        {!selected && (
          <div className="section">
            <h2>Welcome</h2>
            <p className="empty">
              Select a query on the left, or create one with <strong>+ New</strong>.
            </p>
          </div>
        )}

        {selected && (
          <>
            <div className="tabs">
              <div className={`tab ${tab === "runs" ? "active" : ""}`} onClick={() => setTab("runs")}>
                Runs ({runs.length})
              </div>
              <div className={`tab ${tab === "edit" ? "active" : ""}`} onClick={() => setTab("edit")}>
                Edit
              </div>
              <div className={`tab ${tab === "settings" ? "active" : ""}`} onClick={() => setTab("settings")}>
                Settings
              </div>
            </div>

            {tab === "runs" && (
              <>
                <div className="section" style={{ paddingTop: 12, paddingBottom: 12 }}>
                  <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                    <strong>{selected.name}</strong>
                    <span className={`tag ${selected.source_kind}`}>{selected.source_kind}</span>
                    <div style={{ flex: 1 }} />
                    <button
                      className="small"
                      onClick={async () => {
                        const next = { ...selected, enabled: !selected.enabled };
                        await api.updateQuery(selected.id, next);
                        await refreshQueries();
                      }}
                    >
                      {selected.enabled ? "Disable" : "Enable"}
                    </button>
                    <button className="small" onClick={handleTriggerRun}>
                      Run now
                    </button>
                    <button className="small" onClick={() => refreshRuns(selectedId)}>
                      Refresh
                    </button>
                    <button className="small danger" onClick={handleDelete}>
                      Delete
                    </button>
                  </div>
                </div>

                {loadingRuns && runs.length === 0 && <div className="empty">Loading runs...</div>}
                {!loadingRuns && runs.length === 0 && (
                  <div className="empty">
                    No runs yet for this query.
                    <br />
                    Click <strong>Run now</strong> or wait for the scheduler.
                  </div>
                )}
                {runs.map((r) => (
                  <RunCard key={r.id} run={r} />
                ))}
              </>
            )}

            {tab === "edit" && (
              <QueryEditor
                key={selected.id}
                query={selected}
                channels={channels}
                onSave={handleSave}
                onCancel={() => setTab("runs")}
              />
            )}

            {tab === "settings" && (
              <SettingsPanel
                settings={settings}
                onSaved={async () => {
                  await refreshSettings();
                  showToast("Settings saved");
                }}
                showToast={showToast}
              />
            )}
          </>
        )}
      </main>

      {toast && <div className={`toast ${toast.kind}`}>{toast.text}</div>}
    </div>
  );
}
