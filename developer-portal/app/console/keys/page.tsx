import Credentials from "@/components/console/Credentials";
import { adminFetch } from "@/lib/server";
export default async function Keys() {
  const r = await adminFetch("apiKeys");
  if (!r.ok) return <div className="callout warn">Could not load API keys ({r.status}).</div>;
  return <Credentials kind="apiKeys" rows={r.data.data} />;
}
