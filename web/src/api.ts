export type SourceKind = "channels" | "dms" | "mentions";
export type DedupStrategy = "new_messages" | "every_cycle" | "once_per_thread";

export interface Query {
  id: string;
  name: string;
  source_kind: SourceKind;
  source_config: Record<string, unknown>;
  prompt: string;
  interval: string;
  lookback: string;
  dedup: DedupStrategy;
  full_threads: boolean;
  model: string;
  enabled: boolean;
  created_at: number;
  updated_at: number;
}

export interface Run {
  id: string;
  query_id: string;
  channel: string;
  thread_ts: string;
  prompt: string;
  response: string | null;
  error: string | null;
  model: string;
  elapsed_ms: number;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  ran_at: number;
}

export interface Settings {
  llm_base_url: string;
  llm_api_key: string;
  default_model: string;
}

export interface CacheChannel {
  id: string;
  name: string | null;
  is_private: boolean | null;
}

export interface Health {
  ok: boolean;
  scheduler_running: boolean;
  db_path: string;
  cache_db_path: string;
}

const API_BASE = "";

async function jsonOrThrow<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`${resp.status} ${resp.statusText}: ${text}`);
  }
  if (resp.status === 204) return null as T;
  return resp.json() as Promise<T>;
}

export const api = {
  async listQueries(): Promise<Query[]> {
    return jsonOrThrow(await fetch(`${API_BASE}/api/queries`));
  },
  async createQuery(body: Omit<Query, "id" | "created_at" | "updated_at">): Promise<Query> {
    return jsonOrThrow(
      await fetch(`${API_BASE}/api/queries`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
    );
  },
  async updateQuery(id: string, body: Omit<Query, "id" | "created_at" | "updated_at">): Promise<Query> {
    return jsonOrThrow(
      await fetch(`${API_BASE}/api/queries/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
    );
  },
  async deleteQuery(id: string): Promise<void> {
    await jsonOrThrow(await fetch(`${API_BASE}/api/queries/${id}`, { method: "DELETE" }));
  },
  async triggerQuery(id: string): Promise<void> {
    await jsonOrThrow(await fetch(`${API_BASE}/api/queries/${id}/run`, { method: "POST" }));
  },
  async listRuns(queryId?: string, limit = 100, offset = 0): Promise<Run[]> {
    const params = new URLSearchParams();
    if (queryId) params.set("query_id", queryId);
    params.set("limit", String(limit));
    params.set("offset", String(offset));
    return jsonOrThrow(await fetch(`${API_BASE}/api/runs?${params}`));
  },
  async getSettings(): Promise<Settings> {
    return jsonOrThrow(await fetch(`${API_BASE}/api/settings`));
  },
  async updateSettings(body: Partial<Settings>): Promise<Settings> {
    return jsonOrThrow(
      await fetch(`${API_BASE}/api/settings`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
    );
  },
  async listCacheChannels(): Promise<CacheChannel[]> {
    return jsonOrThrow(await fetch(`${API_BASE}/api/cache/channels`));
  },
  async getHealth(): Promise<Health> {
    return jsonOrThrow(await fetch(`${API_BASE}/api/health`));
  },
  async getTemplates(): Promise<Record<string, string>> {
    return jsonOrThrow(await fetch(`${API_BASE}/api/templates`));
  },
};
