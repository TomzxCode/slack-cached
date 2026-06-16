import { useState } from "react";
import type { Settings } from "./api";
import { api } from "./api";

interface Props {
  settings: Settings | null;
  onSaved: () => void;
  showToast: (text: string, kind?: "error" | "success") => void;
}

export function SettingsPanel({ settings, onSaved, showToast }: Props) {
  const [baseUrl, setBaseUrl] = useState(settings?.llm_base_url || "");
  const [apiKey, setApiKey] = useState("");
  const [defaultModel, setDefaultModel] = useState(settings?.default_model || "");
  const [saving, setSaving] = useState(false);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      const patch: Partial<Settings> = {
        llm_base_url: baseUrl,
        default_model: defaultModel,
      };
      // Only send the API key if the user actually typed something. The
      // backend treats strings starting with "*" as "leave alone".
      if (apiKey.trim()) {
        patch.llm_api_key = apiKey.trim();
      }
      await api.updateSettings(patch);
      setApiKey("");
      onSaved();
    } catch (e) {
      showToast((e as Error).message, "error");
    } finally {
      setSaving(false);
    }
  }

  if (!settings) {
    return <div className="section">Loading settings...</div>;
  }

  return (
    <form className="section" onSubmit={handleSave}>
      <h2>Settings</h2>
      <p style={{ color: "var(--muted)", fontSize: 13, marginTop: 0 }}>
        These values are stored in the watcher database and used by all queries.
      </p>

      <div className="form-grid">
        <label className="full">
          <span>LLM base URL (OpenAI-compatible)</span>
          <input
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="https://api.openai.com/v1"
            required
          />
        </label>

        <label className="full">
          <span>
            API key{" "}
            <em style={{ color: "var(--muted)" }}>
              (current: {settings.llm_api_key || "(none)"}; leave blank to keep)
            </em>
          </span>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="sk-..."
            autoComplete="off"
          />
        </label>

        <label className="full">
          <span>Default model (used for new queries)</span>
          <input
            value={defaultModel}
            onChange={(e) => setDefaultModel(e.target.value)}
            placeholder="gpt-4o-mini"
            required
          />
        </label>
      </div>

      <div className="button-row">
        <button type="submit" className="primary" disabled={saving}>
          {saving ? "Saving..." : "Save settings"}
        </button>
      </div>
    </form>
  );
}
