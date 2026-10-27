import { NextRequest, NextResponse } from "next/server";
import { ADMIN_COOKIE, API_URL } from "@/lib/server";

export async function POST(req: NextRequest) {
  const { token } = await req.json().catch(() => ({ token: "" }));
  if (!token) return NextResponse.json({ error: "Token required" }, { status: 400 });
  try {
    const r = await fetch(`${API_URL}/api/v2/admin/clients`, { headers: { "X-Admin-Token": token }, cache: "no-store" });
    if (!r.ok) return NextResponse.json({ error: r.status === 401 || r.status === 403 ? "Invalid admin token" : `API error ${r.status}` }, { status: 401 });
  } catch {
    return NextResponse.json({ error: `API unreachable at ${API_URL}` }, { status: 503 });
  }
  const res = NextResponse.json({ ok: true });
  res.cookies.set(ADMIN_COOKIE, token, { httpOnly: true, sameSite: "strict", secure: process.env.NODE_ENV === "production", path: "/", maxAge: 60 * 60 * 8 });
  return res;
}
export async function DELETE() {
  const res = NextResponse.json({ ok: true });
  res.cookies.delete(ADMIN_COOKIE);
  return res;
}
