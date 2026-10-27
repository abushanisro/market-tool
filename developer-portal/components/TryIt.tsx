"use client";
import { useEffect, useState } from "react";
import { highlight } from "@/lib/highlight";

type P = { name: string; in: string; required: boolean; example?: any };

export default function TryIt({ method, path, params, body, examplePath, exampleQuery, scopes, isForm }: {
  method: string; path: string; params: P[]; body?: any; examplePath?: string; exampleQuery?: Record<string, any>; scopes: string[]; isForm?: boolean;
}) {
  const pathParams = params.filter((p) => p.in === "path");
  const queryParams = params.filter((p) => p.in === "query");
  const derived: Record<string, string> = {};
  if (examplePath) {
    const tpl = path.split("/"), ex = examplePath.split("/");
    tpl.forEach((seg, i) => { if (seg.startsWith("{")) derived[seg.slice(1, -1)] = decodeURIComponent(ex[i] ?? ""); });
  }
  const [values, setValues] = useState<Record<string, string>>(() => {
    const v: Record<string, string> = {};
    pathParams.forEach((p) => (v[p.name] = derived[p.name] ?? (p.example != null ? String(p.example) : "")));
    queryParams.forEach((p) => { const e = exampleQuery?.[p.name]; v[p.name] = e == null ? "" : Array.isArray(e) ? e.join(",") : String(e); });
    return v;
  });
  const [bodyText, setBodyText] = useState(body ? JSON.stringify(body, null, 2) : isForm ? "grant_type=client_credentials&client_id=&client_secret=" : "");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<{ status: number; ms: number; headers: Record<string, string>; text: string } | null>(null);

  useEffect(() => { try { setToken(sessionStorage.getItem("emithran-try-token") ?? ""); } catch { /* ignore */ } }, []);

  const send = async () => {
    setBusy(true); setRes(null);
    let url = path;
    pathParams.forEach((p) => (url = url.replace(`{${p.name}}`, encodeURIComponent(values[p.name] ?? ""))));
    const qs = new URLSearchParams();
    queryParams.forEach((p) => { const v = values[p.name]; if (v) v.split(",").forEach((x) => qs.append(p.name, x.trim())); });
    try { sessionStorage.setItem("emithran-try-token", token); } catch { /* ignore */ }
    const t0 = performance.now();
    try {
      const r = await fetch(`/api/try?target=${encodeURIComponent(url + (qs.toString() ? "?" + qs : ""))}`, {
        method, headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...(bodyText && method !== "GET" ? { "Content-Type": isForm ? "application/x-www-form-urlencoded" : "application/json" } : {}) },
        body: method !== "GET" && bodyText ? bodyText : undefined,
      });
      const text = await r.text();
      const headers: Record<string, string> = {};
      r.headers.forEach((v, k) => { if (k.startsWith("x-ratelimit") || k === "content-type" || k === "x-emithran-api-version") headers[k] = v; });
      let pretty = text;
      try { pretty = JSON.stringify(JSON.parse(text), null, 2); } catch { /* not JSON */ }
      setRes({ status: r.status, ms: Math.round(performance.now() - t0), headers, text: pretty.slice(0, 60000) });
    } catch (e) {
      setRes({ status: 0, ms: 0, headers: {}, text: String(e) });
    } finally { setBusy(false); }
  };

  return (
    <section className="try" id="try-it">
      <div className="try-h"><b>Try it</b><span className="note">Sends a real request to this deployment{scopes.length ? ` · needs ${scopes.join(", ")}` : ""}</span></div>
      <div className="try-b">
        {scopes.length > 0 && (
          <div className="field"><label htmlFor="tok">Bearer token</label>
            <input id="tok" className="input" type="password" placeholder="emt_… or emk_…" value={token} onChange={(e) => setToken(e.target.value)} />
            <span className="hint">Kept only in this browser tab (sessionStorage). Create a key in the Developer Console.</span></div>
        )}
        {[...pathParams, ...queryParams].length > 0 && (
          <div className="grid2">
            {[...pathParams, ...queryParams].map((p) => (
              <div className="field" key={p.name}><label htmlFor={`p-${p.name}`}>{p.name}{p.required ? " *" : ""} <span className="note">({p.in})</span></label>
                <input id={`p-${p.name}`} className="input" value={values[p.name] ?? ""} onChange={(e) => setValues({ ...values, [p.name]: e.target.value })} /></div>
            ))}
          </div>
        )}
        {method !== "GET" && (
          <div className="field"><label htmlFor="body">{isForm ? "Form body" : "Request body (JSON)"}</label>
            <textarea id="body" className="input" value={bodyText} onChange={(e) => setBodyText(e.target.value)} /></div>
        )}
        <div className="row"><button className="btn primary" onClick={send} disabled={busy}>{busy ? "Sending…" : `Send ${method}`}</button>
          {res && <span className="note">{res.status ? `${res.status} · ${res.ms} ms` : "network error"} {Object.entries(res.headers).map(([k, v]) => `· ${k}: ${v}`).join(" ")}</span>}</div>
        {res && <div className="codebox" style={{ margin: 0 }}><div className="codebox-h"><span className="title">Response<span className={`status ${res.status >= 400 || !res.status ? "e" : ""}`}>{res.status || "ERR"}</span></span></div>
          <pre><code dangerouslySetInnerHTML={{ __html: highlight(res.text, "json") }} /></pre></div>}
      </div>
    </section>
  );
}
