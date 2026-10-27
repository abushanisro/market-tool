import { NextRequest, NextResponse } from "next/server";
import { adminFetch } from "@/lib/server";

/** Console actions (create/rotate/revoke) — forwarded to /api/v2/admin/* with the admin token from the httpOnly cookie. */
async function handle(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const allowed = /^(clients(\/[\w-]+\/(rotateSecret|revoke))?|apiKeys(\/[\w-]+\/revoke)?|usage(\/requests)?)$/;
  const p = path.join("/");
  if (!allowed.test(p)) return NextResponse.json({ errorName: "NotAllowed" }, { status: 400 });
  const r = await adminFetch(p + (req.nextUrl.search || ""), { method: req.method, body: req.method === "POST" ? await req.text() : undefined });
  return NextResponse.json(r.data, { status: r.status });
}
export const GET = handle;
export const POST = handle;
