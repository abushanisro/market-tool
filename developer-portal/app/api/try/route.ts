import { NextRequest, NextResponse } from "next/server";
import { API_URL } from "@/lib/server";

/** Server-side relay for the "Try it" panel: forwards to the public API only (never admin endpoints). */
async function relay(req: NextRequest) {
  const target = req.nextUrl.searchParams.get("target") ?? "";
  if (!target.startsWith("/api/v2/") || target.startsWith("/api/v2/admin") || target.includes("..")) {
    return NextResponse.json({ errorCode: "INVALID_ARGUMENT", errorName: "InvalidTarget", parameters: { target } }, { status: 400 });
  }
  const headers: Record<string, string> = {};
  for (const h of ["authorization", "content-type"]) { const v = req.headers.get(h); if (v) headers[h] = v; }
  try {
    const r = await fetch(API_URL + target, { method: req.method, headers, body: req.method === "GET" ? undefined : await req.text(), cache: "no-store" });
    const out = new NextResponse(await r.arrayBuffer(), { status: r.status });
    for (const h of ["content-type", "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset", "x-emithran-api-version", "retry-after"]) {
      const v = r.headers.get(h); if (v) out.headers.set(h, v);
    }
    return out;
  } catch {
    return NextResponse.json({ errorCode: "INTERNAL", errorName: "ApiUnavailable", parameters: { apiUrl: API_URL } }, { status: 503 });
  }
}
export const GET = relay;
export const POST = relay;
