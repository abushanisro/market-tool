import { adminFetch } from "@/lib/server";

export default async function Usage({ searchParams }: { searchParams: Promise<{ days?: string }> }) {
  const days = Math.min(90, Math.max(1, Number((await searchParams).days ?? 14) || 14));
  const [u, rec] = await Promise.all([adminFetch(`usage?days=${days}`), adminFetch("usage/requests?limit=25&errorsOnly=true")]);
  if (!u.ok) return <div className="callout warn">Could not load usage ({u.status}).</div>;
  const d = u.data;
  const byDay = new Map<string, any>(d.byDay.map((x: any) => [x.day, x]));
  const series = Array.from({ length: days }, (_, i) => {
    const day = new Date(Date.now() - (days - 1 - i) * 864e5).toISOString().slice(0, 10);
    return byDay.get(day) ?? { day, requests: 0, errors: 0 };
  });
  d.byDay = series;
  const max = Math.max(1, ...d.byDay.map((x: any) => x.requests));
  return (
    <>
      <div className="row" style={{ marginBottom: 14 }}>{[1, 7, 14, 30, 90].map((n) => <a key={n} className={`btn sm ${n === days ? "primary" : ""}`} href={`/console/usage?days=${n}`}>{n} d</a>)}</div>
      <div className="kpis">
        <div className="card"><div className="num">{d.total.requests.toLocaleString("en-US")}</div><p>Requests</p></div>
        <div className="card"><div className="num">{d.total.errors.toLocaleString("en-US")}</div><p>Errors (4xx/5xx)</p></div>
        <div className="card"><div className="num">{d.total.avgLatencyMs ?? "—"}<span className="note"> ms</span></div><p>Average latency</p></div>
        <div className="card"><div className="num">{d.total.p95LatencyMs ?? "—"}<span className="note"> ms</span></div><p>p95 latency</p></div>
      </div>
      <div className="panel"><div className="panel-h"><h2>Requests per day</h2><span className="note"><span className="pill">requests</span> <span className="pill e">errors</span></span></div>
        <div className="panel-b">{d.byDay.some((x: any) => x.requests) ? (<>
          <div className="bars" role="img" aria-label="Requests per day">{d.byDay.map((x: any) => (
            <div key={x.day} className="b" style={{ height: `${(x.requests / max) * 100}%` }} title={`${x.day}: ${x.requests} requests, ${x.errors} errors`}>
              <div className="e" style={{ height: `${x.requests ? (x.errors / x.requests) * 100 : 0}%` }} /></div>))}</div>
          <div className="bars-x">{d.byDay.map((x: any, i: number) => <span key={x.day}>{i % Math.ceil(days / 14) === 0 ? x.day.slice(5) : ""}</span>)}</div></>) : <p className="note">No requests in this period.</p>}</div></div>
      <div className="panel"><div className="panel-h"><h2>By endpoint</h2></div><div style={{ overflowX: "auto" }}>
        <table className="tbl"><thead><tr><th>Endpoint</th><th>Requests</th><th>Errors</th><th>Avg ms</th><th>p95 ms</th></tr></thead><tbody>
          {d.byEndpoint.map((x: any) => <tr key={x.endpoint}><td><code style={{ fontSize: 12.5 }}>{x.endpoint}</code></td><td className="num">{x.requests}</td><td className="num">{x.errors}</td><td className="num">{x.avgLatencyMs}</td><td className="num">{x.p95LatencyMs}</td></tr>)}
        </tbody></table></div></div>
      <div className="grid2" style={{ alignItems: "start" }}>
        <div className="panel"><div className="panel-h"><h2>By principal</h2></div>
          <table className="tbl"><thead><tr><th>Principal</th><th>Requests</th><th>Errors</th></tr></thead><tbody>
            {d.byPrincipal.map((x: any) => <tr key={x.principal}><td><code style={{ fontSize: 12.5 }}>{x.principal}</code></td><td className="num">{x.requests}</td><td className="num">{x.errors}</td></tr>)}</tbody></table></div>
        <div className="panel"><div className="panel-h"><h2>By status</h2></div>
          <table className="tbl"><tbody>{Object.entries<number>(d.byStatus).sort().map(([s, n]) => <tr key={s}><td><span className={`pill ${Number(s) >= 400 ? "e" : ""}`}>{s}</span></td><td className="num">{n}</td></tr>)}</tbody></table></div>
      </div>
      <div className="panel"><div className="panel-h"><h2>Recent errors</h2><span className="note">quote errorInstanceId when reporting</span></div><div style={{ overflowX: "auto" }}>
        <table className="tbl"><thead><tr><th>Time</th><th>Principal</th><th>Request</th><th>Status</th><th>errorName</th><th>errorInstanceId</th></tr></thead><tbody>
          {(rec.data?.data ?? []).map((x: any, i: number) => <tr key={i}><td className="note">{new Date(x.ts).toLocaleString()}</td><td><code style={{ fontSize: 12 }}>{x.principal}</code></td>
            <td><code style={{ fontSize: 12 }}>{x.method} {x.route}</code></td><td><span className="pill e">{x.status}</span></td><td>{x.errorName ?? "—"}</td><td><code style={{ fontSize: 11 }}>{x.errorInstanceId ?? "—"}</code></td></tr>)}
          {!(rec.data?.data ?? []).length && <tr><td colSpan={6} className="note" style={{ textAlign: "center", padding: 24 }}>No errors.</td></tr>}
        </tbody></table></div></div>
    </>
  );
}
