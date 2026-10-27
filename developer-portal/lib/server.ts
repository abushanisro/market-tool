import "server-only";
import { cookies } from "next/headers";

export const API_URL = (process.env.MIP_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
export const ADMIN_COOKIE = "emithran_admin";

export async function adminToken(): Promise<string | null> {
  return (await cookies()).get(ADMIN_COOKIE)?.value ?? null;
}

export async function adminFetch(path: string, init: RequestInit = {}) {
  const tok = await adminToken();
  if (!tok) return { ok: false as const, status: 401, data: null };
  try {
    const r = await fetch(`${API_URL}/api/v2/admin/${path.replace(/^\//, "")}`, {
      ...init, cache: "no-store", headers: { ...(init.headers ?? {}), "X-Admin-Token": tok, "Content-Type": "application/json" },
    });
    const data = await r.json().catch(() => null);
    return { ok: r.ok, status: r.status, data };
  } catch {
    return { ok: false as const, status: 503, data: { errorName: "ApiUnavailable", parameters: { apiUrl: API_URL } } };
  }
}
