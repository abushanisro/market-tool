import "server-only";
import { auth0 } from "./auth0";

export const API_URL = (process.env.EMITHRAN_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

/** Admin call to the backend as the signed-in user (their Auth0 access token); the backend allows it only for the `admin` role. */
export async function adminFetch(path: string, init: RequestInit = {}) {
  let token: string;
  try {
    token = (await auth0.getAccessToken()).token;
  } catch {
    return { ok: false as const, status: 401, data: null };
  }
  try {
    const r = await fetch(`${API_URL}/api/v2/admin/${path.replace(/^\//, "")}`, {
      ...init, cache: "no-store", headers: { ...(init.headers ?? {}), Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    });
    const data = await r.json().catch(() => null);
    return { ok: r.ok, status: r.status, data };
  } catch {
    return { ok: false as const, status: 503, data: { errorName: "ApiUnavailable", parameters: { apiUrl: API_URL } } };
  }
}
