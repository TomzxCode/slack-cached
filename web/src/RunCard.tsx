import type { Run } from "./api";

function formatTime(epoch: number): string {
  const d = new Date(epoch * 1000);
  return d.toLocaleString();
}

function permalink(channel: string, threadTs: string): string {
  const pts = threadTs.replace(".", "");
  return `https://slack.com/archives/${channel}/p${pts}`;
}

export function RunCard({ run }: { run: Run }) {
  return (
    <div className="run">
      <header>
        <a className="permalink" href={permalink(run.channel, run.thread_ts)} target="_blank" rel="noreferrer">
          {run.channel}/{run.thread_ts}
        </a>
        <span style={{ fontSize: 11, color: "var(--muted)" }}>{formatTime(run.ran_at)}</span>
      </header>
      {run.error ? (
        <div className="response error">{run.error}</div>
      ) : (
        <div className="response">{run.response || "(empty response)"}</div>
      )}
      <div className="meta">
        <span>model: {run.model}</span>
        <span>{run.elapsed_ms} ms</span>
        {run.prompt_tokens != null && <span>prompt tokens: {run.prompt_tokens}</span>}
        {run.completion_tokens != null && <span>completion tokens: {run.completion_tokens}</span>}
      </div>
      <details>
        <summary>Show prompt</summary>
        <pre>{run.prompt}</pre>
      </details>
    </div>
  );
}
