import { NextRequest, NextResponse } from "next/server";
import { auth0 } from "@/lib/auth0";

// Next.js 16 "proxy" (formerly middleware): mounts the Auth0 routes and requires a signed-in user for everything private.
// Docs and the login routes stay public. Roles are enforced by the backend from the access token, not here.
const PRIVATE = /^\/(dashboard|console|api\/emithran|api\/console)(\/|$)/;

export async function proxy(req: NextRequest) {
  const { pathname, search } = req.nextUrl;
  // exact match on purpose: config redirects also match "/dashboard/" and would loop. The dashboard needs the slash for its relative asset paths.
  if (pathname === "/dashboard") return NextResponse.redirect(new URL("/dashboard/" + search, req.url), 308);
  const res = await auth0.middleware(req);
  if (pathname.startsWith("/auth/") || !PRIVATE.test(pathname)) return res;
  if (await auth0.getSession(req)) return res;
  if (pathname.startsWith("/api/")) return NextResponse.json({ error: "Sign in required" }, { status: 401 });
  return NextResponse.redirect(new URL(`/auth/login?returnTo=${encodeURIComponent(pathname + search)}`, req.url));
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon\\.(?:png|svg)|logo\\.png).*)"] };
