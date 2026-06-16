import { useState } from "react";
import type { Query, SourceKind, DedupStrategy, CacheChannel } from "./api";
import { api } from "./api";

interface Props {
  query: Query;
  channels: CacheChannel[];
  onSave: (q: Omit<Query, "id" | "created_at" | "updated_at">) => void;
  onCancel: () => void;
}

const TEMPLATES_LOAD_BUTTON = "Load template...";

export function QueryEditor({ query, channels, onSave, onCancel }: Props) {
  const [name, setName] = useState(query.name);
  const [sourceKind, setSourceKind] = useState<SourceKind>(query.source_kind);
  const [channelIds, setChannelIds] = useState<string[]>(
    (query.source_config.channel_ids as string[]) || [],
  );
  const [includeMpim, setIncludeMpim] = useState<boolean>(
    Boolean(query.source_config.include_mpim ?? true),
  );
  const [prompt, setPrompt] = useState(query.prompt);
  const [interval, setInterval] = useState(query.interval);
  const [lookback, setLookback] = useState(query.lookback);
  const [dedup, setDedup] = useState<DedupStrategy>(query.dedup);
  const [fullThreads, setFullThreads] = useState(query.full_threads);
  const [model, setModel] = useState(query.model);
  const [enabled, setEnabled] = useState(query.enabled);
  const [channelInput, setChannelInput] = useState("");
  const [templates, setTemplates] = useState<Record<string, string> | null>(null);

  async function loadTemplates() {
    if (templates) return;
    try {
      setTemplates(await api.getTemplates());
    } catch {
      setTemplates({});
    }
  }

  function addChannel(id: string) {
    const trimmed = id.trim();
    if (!trimmed) return;
    if (channelIds.includes(trimmed)) return;
    setChannelIds([...channelIds, trimmed]);
    setChannelInput("");
  }

  function removeChannel(id: string) {
    setChannelIds(channelIds.filter((c) => c !== id));
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const sourceConfig: Record<string, unknown> =
      sourceKind === "channels"
        ? { channel_ids: channelIds }
        : sourceKind === "dms"
          ? { include_mpim: includeMpim }
          : {};
    onSave({
      name,
      source_kind: sourceKind,
      source_config: sourceConfig,
      prompt,
      interval,
      lookback,
      dedup,
      full_threads: fullThreads,
      model,
      enabled,
    });
  }

  return (
    <form className="section" onSubmit={handleSubmit}>
      <h2>Edit query</h2>

      <div className="form-grid">
        <label className="full">
          <span>Name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} required />
        </label>

        <label>
          <span>Source</span>
          <select value={sourceKind} onChange={(e) => setSourceKind(e.target.value as SourceKind)}>
            <option value="channels">channels (explicit list)</option>
            <option value="dms">dms (your DM / mpim channels)</option>
            <option value="mentions">mentions (best-effort scan)</option>
          </select>
        </label>

        <label>
          <span>Model</span>
          <input value={model} onChange={(e) => setModel(e.target.value)} required />
        </label>

        {sourceKind === "channels" && (
          <div className="full">
            <span style={{ fontSize: 12, color: "var(--muted)" }}>Channels</span>
            <div className="chips" style={{ marginBottom: 6, marginTop: 4 }}>
              {channelIds.map((id) => {
                const cached = channels.find((c) => c.id === id);
                return (
                  <span className="chip" key={id}>
                    {cached?.name ? `${cached.name} (${id})` : id}
                    <button type="button" onClick={() => removeChannel(id)}>
                      x
                    </button>
                  </span>
                );
              })}
              {channelIds.length === 0 && (
                <span style={{ color: "var(--muted)", fontSize: 12 }}>No channels added.</span>
              )}
            </div>
            <div className="channel-picker">
              <input
                list="cached-channels"
                placeholder="C0001 or start typing..."
                value={channelInput}
                onChange={(e) => setChannelInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addChannel(channelInput);
                  }
                }}
              />
              <datalist id="cached-channels">
                {channels.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name || ""}
                  </option>
                ))}
              </datalist>
              <button type="button" onClick={() => addChannel(channelInput)}>
                Add
              </button>
            </div>
            {channels.length > 0 && (
              <div style={{ marginTop: 8 }}>
                <details>
                  <summary style={{ fontSize: 11, color: "var(--muted)", cursor: "pointer" }}>
                    Pick from cached channels ({channels.length})
                  </summary>
                  <div className="chips" style={{ marginTop: 6 }}>
                    {channels
                      .filter((c) => !channelIds.includes(c.id))
                      .map((c) => (
                        <button
                          type="button"
                          key={c.id}
                          className="chip"
                          onClick={() => addChannel(c.id)}
                          title={c.id}
                        >
                          + {c.name || c.id}
                        </button>
                      ))}
                  </div>
                </details>
              </div>
            )}
          </div>
        )}

        {sourceKind === "dms" && (
          <label className="full">
            <span>Include multi-party IMs (mpim)</span>
            <input
              type="checkbox"
              checked={includeMpim}
              onChange={(e) => setIncludeMpim(e.target.checked)}
            />
          </label>
        )}

        <label className="full">
          <span>
            Prompt template <em style={{ color: "var(--muted)" }}>(use {`{{thread}}`}, {`{{channel}}`}, {`{{permalink}}`})</em>
          </span>
          <div style={{ display: "flex", gap: 8, marginBottom: 6 }}>
            <select
              value={TEMPLATES_LOAD_BUTTON}
              onChange={async (e) => {
                if (e.target.value === TEMPLATES_LOAD_BUTTON) return;
                const tpl = (templates || (await api.getTemplates()))[e.target.value];
                if (tpl) setPrompt(tpl);
              }}
              onClick={loadTemplates}
              style={{ width: "auto", flex: 0 }}
            >
              <option>{TEMPLATES_LOAD_BUTTON}</option>
              {templates &&
                Object.entries(templates).map(([k, v]) => (
                  <option key={k} value={k}>
                    {k} ({v.slice(0, 40)}...)
                  </option>
                ))}
            </select>
          </div>
          <textarea rows={10} value={prompt} onChange={(e) => setPrompt(e.target.value)} required />
        </label>

        <label>
          <span>Interval (poll cadence)</span>
          <input value={interval} onChange={(e) => setInterval(e.target.value)} required placeholder="5m" />
        </label>

        <label>
          <span>Lookback window</span>
          <input value={lookback} onChange={(e) => setLookback(e.target.value)} required placeholder="1h" />
        </label>

        <label>
          <span>Dedup strategy</span>
          <select value={dedup} onChange={(e) => setDedup(e.target.value as DedupStrategy)}>
            <option value="new_messages">new messages (re-run when thread has new replies)</option>
            <option value="every_cycle">every cycle (run on every poll)</option>
            <option value="once_per_thread">once per thread (never re-run)</option>
          </select>
        </label>

        <label>
          <span>Full threads</span>
          <input
            type="checkbox"
            checked={fullThreads}
            onChange={(e) => setFullThreads(e.target.checked)}
          />
        </label>

        <label>
          <span>Enabled</span>
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
        </label>
      </div>

      <div className="button-row">
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
        <button type="submit" className="primary">
          Save
        </button>
      </div>
    </form>
  );
}
