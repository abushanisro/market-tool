"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import ScopePicker from "./ScopePicker";

type Row = Record<string, any>;
const fmt = (s?: string | null) => (s ? new Date(s).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—");

export default function Credentials({ kind, rows }: { kind: "clients" | "apiKeys"; rows: Row[] }) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [owner, setOwner] = useState("");
  const [scopes, setScopes] = useState<string[]>(["api:ontologies-read", "api:market-read"]);
  const [rate, setRate] = useState(600);
  const [secret, setSecret] = useState<{ label: string; value: string; id?: string } | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const isClient = kind === "clients";

  const call = async (path: string, body?: unknown) => {
    setBusy(true); setErr("");
    const r = await fetch(`/api/console/${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });
    const data = await r.json().catch(() => ({}));
    setBusy(false);
    if (!r.ok) { setErr(`${data?.errorName ?? "Error"} ${data?.parameters ? JSON.stringify(data.parameters) : ""}`); return null; }
    router.refresh();
    return data;
  };
  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    const d = await call(kind, isClient ? { name, owner: owner || null, scopes, rateLimitPerMinute: rate } : { name, owner: owner || null, scopes, rateLimitPerMinute: rate, expiresInDays: 90 });
    if (d) { setSecret(isClient ? { label: "Client secret", value: d.clientSecret, id: d.clientId } : { label: "API key", value: d.apiKey }); setName(""); }
  };

  return (
    <>
      {secret && (
        <div className="panel" style={{ borderColor: "var(--warn)" }}>
          <div className="panel-h"><h2>Copy your {secret.label.toLowerCase()} now</h2><button className="btn sm" onClick={() => setSecret(null)}>Done</button></div>
          <div className="panel-b" style={{ display: "grid", gap: 10 }}>
            {secret.id && <div>Client ID: <code>{secret.id}</code></div>}
            <div className="secret">{secret.value}</div>
            <div className="row"><button className="btn sm" onClick={() => navigator.clipboard.writeText(secret.value)}>Copy</button>
              <span className="note">It is stored only as a SHA-256 hash and cannot be shown again.</span></div>
          </div>
        </div>
      )}
      <div className="panel">
        <div className="panel-h"><h2>{isClient ? "Create OAuth client" : "Create API key"}</h2></div>
        <form className="panel-b" onSubmit={create} style={{ display: "grid", gap: 14 }}>
          <div className="grid2">
            <div className="field"><label htmlFor="nm">Name</label><input id="nm" className="input" required minLength={2} value={name} onChange={(e) => setName(e.target.value)} placeholder={isClient ? "Costing service" : "Analyst notebook"} /></div>
            <div className="field"><label htmlFor="ow">Owner</label><input id="ow" className="input" value={owner} onChange={(e) => setOwner(e.target.value)} placeholder="team or e-mail" /></div>
          </div>
          <div className="field"><label>Scopes</label><ScopePicker value={scopes} onChange={setScopes} /></div>
          <div className="field" style={{ maxWidth: 260 }}><label htmlFor="rl">Rate limit (requests / minute)</label><input id="rl" type="number" min={1} className="input" value={rate} onChange={(e) => setRate(+e.target.value)} /></div>
          {err && <div className="callout warn">{err}</div>}
          <div><button className="btn primary" disabled={busy || !name || !scopes.length}>{isClient ? "Create client" : "Create key"}</button>
            {!isClient && <span className="note" style={{ marginLeft: 10 }}>Keys expire after 90 days.</span>}</div>
        </form>
      </div>
      <div className="panel">
        <div className="panel-h"><h2>{isClient ? "OAuth clients" : "API keys"}</h2><span className="note">{rows.length}</span></div>
        <div style={{ overflowX: "auto" }}>
          <table className="tbl"><thead><tr>
            <th>{isClient ? "Client" : "Key"}</th><th>Scopes</th><th>Rate / min</th><th>{isClient ? "Last token" : "Last used"}</th><th>Created</th><th>Status</th><th></th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.rid}>
                  <td><b>{r.name}</b><div className="note"><code>{isClient ? r.clientId : r.prefix}</code>{isClient && <> · secret {r.secretHint}</>}{r.owner ? ` · ${r.owner}` : ""}</div></td>
                  <td>{(r.scopes as string[]).map((s) => <div key={s}><code style={{ fontSize: 12 }}>{s}</code></div>)}</td>
                  <td className="num">{r.rateLimitPerMinute}</td>
                  <td>{fmt(isClient ? r.lastTokenAt : r.lastUsedAt)}</td>
                  <td>{fmt(r.createdAt)}{!isClient && r.expiresAt && <div className="note">expires {fmt(r.expiresAt)}</div>}</td>
                  <td><span className={`pill ${r.status === "ACTIVE" ? "" : "off"}`}>{r.status}</span></td>
                  <td style={{ whiteSpace: "nowrap" }}>{r.status === "ACTIVE" && (<div className="row">
                    {isClient && <button className="btn sm" disabled={busy} onClick={async () => { const d = await call(`clients/${r.clientId}/rotateSecret`); if (d) setSecret({ label: "Client secret", value: d.clientSecret, id: d.clientId }); }}>Rotate secret</button>}
                    <button className="btn sm danger" disabled={busy} onClick={() => { if (confirm(`Revoke ${r.name}? Integrations using it stop working immediately.`)) call(isClient ? `clients/${r.clientId}/revoke` : `apiKeys/${String(r.prefix).replace(/^emk_|_…$/g, "")}/revoke`); }}>Revoke</button></div>)}</td>
                </tr>
              ))}
              {!rows.length && <tr><td colSpan={7} className="note" style={{ textAlign: "center", padding: 28 }}>None yet — create one above.</td></tr>}
            </tbody></table>
        </div>
      </div>
    </>
  );
}
