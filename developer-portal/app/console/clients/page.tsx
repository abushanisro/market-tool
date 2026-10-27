import Credentials from "@/components/console/Credentials";
import { adminFetch } from "@/lib/server";
export default async function Clients() {
  const r = await adminFetch("clients");
  if (!r.ok) return <div className="callout warn">Could not load clients ({r.status}).</div>;
  return <Credentials kind="clients" rows={r.data.data} />;
}
