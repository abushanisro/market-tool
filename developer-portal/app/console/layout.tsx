import Link from "next/link";
import Login from "@/components/console/Login";
import SignOut from "@/components/console/SignOut";
import { API_URL, adminToken } from "@/lib/server";

export const dynamic = "force-dynamic";
export const metadata = { title: "Developer Console" };

export default async function ConsoleLayout({ children }: { children: React.ReactNode }) {
  const tok = await adminToken();
  return (
    <div className="console">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-end", marginBottom: 18 }}>
        <div><div className="note" style={{ textTransform: "uppercase", letterSpacing: ".08em", fontWeight: 600, color: "var(--brand-strong)" }}>Developer Console</div>
          <h1>Credentials &amp; usage</h1></div>
        {tok && <SignOut />}
      </div>
      {!tok ? <Login /> : (<>
        <nav className="row" style={{ gap: 6, marginBottom: 20 }}>
          <Link className="btn sm" href="/console">Overview</Link><Link className="btn sm" href="/console/clients">OAuth clients</Link>
          <Link className="btn sm" href="/console/keys">API keys</Link><Link className="btn sm" href="/console/usage">Usage</Link>
        </nav>
        {children}
      </>)}
      <p className="note" style={{ marginTop: 28 }}>Connected to <code>{API_URL}</code></p>
    </div>
  );
}
