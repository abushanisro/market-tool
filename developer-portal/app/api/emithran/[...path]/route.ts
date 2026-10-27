import { NextRequest, NextResponse } from "next/server";
import { auth0 } from "@/lib/auth0";
import { API_URL } from "@/lib/server";

export const dynamic = "force-dynamic";
export const maxDuration = 300; // the live-update stream stays open as long as the plan allows; the browser reconnects by itself

// The dashboard talks only to this route. It adds the signed-in user's access token and forwards to the backend, streaming the
// reply (live updates). Never exposed: the worker control plane (/api/fetcher) and the machine-client API (/api/v2).
const ALLOWED = /^api\/(?!fetcher(\/|$)|v2(\/|$))/;

async function forward(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const path = (await ctx.params).path.join("/");
  if (!ALLOWED.test(path)) return NextResponse.json({ error: "Not found" }, { status: 404 });
  let token: string;
  try {
    token = (await auth0.getAccessToken()).token;
  } catch {
    return NextResponse.json({ error: "Sign in required" }, { status: 401 });
  }
  const upstream = await fetch(`${API_URL}/${path}${req.nextUrl.search}`, {
    method: req.method,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": req.headers.get("content-type") ?? "application/json", Accept: req.headers.get("accept") ?? "*/*" },
    body: req.method === "GET" || req.method === "HEAD" ? undefined : await req.text(),
    cache: "no-store",
    signal: req.signal,
  });
  return new Response(upstream.body, {
    status: upstream.status,
    headers: { "Content-Type": upstream.headers.get("content-type") ?? "application/json", "Cache-Control": "no-store" },
  });
}

export { forward as GET, forward as POST, forward as PUT, forward as PATCH, forward as DELETE };
