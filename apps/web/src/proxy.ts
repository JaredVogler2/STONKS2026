import { NextResponse, type NextRequest } from "next/server";

/*
 * Perimeter checks that run before every route:
 *  - Vercel's protection-bypass secret is only honoured for the signed machine routes (/api/v1/agent/*, /api/v1/release/*);
 *    anywhere else a request carrying it is refused, so a leaked bypass value cannot open the dashboard.
 *  - Pages need an owner session cookie (cryptographic verification happens server-side on every request).
 */
const MACHINE = /^\/api\/v1\/(agent|release)\//;
const PUBLIC = /^\/(login|api\/auth\/|_next\/|favicon|icon|apple-icon|robots\.txt)/;

export function proxy(req: NextRequest) {
  const path = req.nextUrl.pathname;
  const bypass = req.headers.get("x-vercel-protection-bypass") ?? req.nextUrl.searchParams.get("x-vercel-protection-bypass");
  if (bypass && !MACHINE.test(path)) {
    return new NextResponse("bypass not permitted for this route", { status: 403 });
  }
  if (MACHINE.test(path) || PUBLIC.test(path) || path.startsWith("/api/")) {
    const res = NextResponse.next();
    res.headers.set("X-Robots-Tag", "noindex, nofollow");
    return res;
  }
  if (!req.cookies.get("apex_session")) {
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    url.search = "";
    return NextResponse.redirect(url);
  }
  const res = NextResponse.next();
  res.headers.set("X-Robots-Tag", "noindex, nofollow");
  return res;
}

export const config = { matcher: ["/((?!_next/static|_next/image).*)"] };
