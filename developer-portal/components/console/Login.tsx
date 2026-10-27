"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";

export default function Login() {
  const [token, setToken] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const router = useRouter();
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setErr("");
    const r = await fetch("/api/console-auth", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token }) });
    setBusy(false);
    if (r.ok) router.refresh(); else setErr((await r.json()).error ?? "Sign-in failed");
  };
  return (
    <form onSubmit={submit} className="panel" style={{ maxWidth: 520 }}>
      <div className="panel-h"><h2>Sign in to the Developer Console</h2></div>
      <div className="panel-b" style={{ display: "grid", gap: 12 }}>
        <p className="note" style={{ margin: 0 }}>Enter the service administrator token (<code>EMITHRAN_ADMIN_TOKEN</code>). It is kept in an httpOnly cookie for 8 hours and never exposed to page scripts.</p>
        <div className="field"><label htmlFor="adm">Admin token</label><input id="adm" type="password" className="input" value={token} onChange={(e) => setToken(e.target.value)} autoComplete="off" /></div>
        {err && <div className="callout warn">{err}</div>}
        <div><button className="btn primary" disabled={busy || !token}>{busy ? "Checking…" : "Sign in"}</button></div>
      </div>
    </form>
  );
}
