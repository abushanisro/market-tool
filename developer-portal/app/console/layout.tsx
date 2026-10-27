import Link from "next/link";
import { API_URL } from "@/lib/server";

export const dynamic = "force-dynamic";
export const metadata = { title: "Developer Console" };

// proxy.ts guarantees a signed-in user here; the backend decides whether they are an admin.
export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="console">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-end", marginBottom: 18 }}>
        <div><div className="note" style={{ textTransform: "uppercase", letterSpacing: ".08em", fontWeight: 600, color: "var(--brand-strong)" }}>Developer Console</div>
          <h1>Credentials &amp; usage</h1></div>
        <a className="btn sm" href="/auth/logout">Sign out</a>
      </div>
      <nav className="row" style={{ gap: 6, marginBottom: 20 }}>
        <Link className="btn sm" href="/console">Overview</Link><Link className="btn sm" href="/console/clients">OAuth clients</Link>
        <Link className="btn sm" href="/console/keys">API keys</Link><Link className="btn sm" href="/console/usage">Usage</Link>
      </nav>
      {children}
      <p className="note" style={{ marginTop: 28 }}>Connected to <code>{API_URL}</code></p>
    </div>
  );
}
