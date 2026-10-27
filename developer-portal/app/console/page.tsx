import Link from "next/link";
import { adminFetch } from "@/lib/server";

export default async function ConsoleHome() {
  const [u, c, k] = await Promise.all([adminFetch("usage?days=7"), adminFetch("clients"), adminFetch("apiKeys")]);
  if (!u.ok) return <div className="callout warn">Could not load usage ({u.status}). {u.data?.errorName ?? ""}</div>;
  const t = u.data.total;
  const active = (rows: any[]) => rows.filter((r) => r.status === "ACTIVE").length;
  return (
    <>
      <div className="kpis">
        <div className="card"><div className="num">{t.requests.toLocaleString("en-US")}</div><p>Requests (7 days)</p></div>
        <div className="card"><div className="num">{t.requests ? ((t.errors / t.requests) * 100).toFixed(1) : "0.0"}%</div><p>Error rate</p></div>
        <div className="card"><div className="num">{t.p95LatencyMs ?? "—"}<span className="note"> ms</span></div><p>p95 latency</p></div>
        <div className="card"><div className="num">{active(c.data?.data ?? [])} / {active(k.data?.data ?? [])}</div><p>Active clients / keys</p></div>
      </div>
      <div className="cards">
        <Link className="card" href="/console/clients"><h3>OAuth clients</h3><p>Server-to-server credentials using the client-credentials grant.</p></Link>
        <Link className="card" href="/console/keys"><h3>API keys</h3><p>Bearer keys for scripts and notebooks; expire after 90 days.</p></Link>
        <Link className="card" href="/console/usage"><h3>Usage</h3><p>Requests, errors and latency per day, endpoint and principal.</p></Link>
        <Link className="card" href="/docs/general/getting-started"><h3>Getting started</h3><p>Make your first call in five minutes.</p></Link>
      </div>
    </>
  );
}
