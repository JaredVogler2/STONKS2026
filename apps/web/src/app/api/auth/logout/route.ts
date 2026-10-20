import { NextResponse } from "next/server";
import { clearedSessionCookie, ownerFromToken } from "@/server/auth/owner";
import { getDb } from "@/server/db";
import { SESSION_COOKIE } from "@/server/security/session";

export const runtime = "nodejs";

export async function POST(req: Request) {
  const cookie = (req.headers.get("cookie") ?? "").split(";").map((c) => c.trim()).find((c) => c.startsWith(`${SESSION_COOKIE}=`));
  const db = await getDb();
  const owner = await ownerFromToken(db, cookie?.slice(SESSION_COOKIE.length + 1));
  if (owner) await db.query("UPDATE apex.admin_sessions SET revoked_at = now() WHERE id = $1", [owner.sessionId]);
  const res = NextResponse.redirect(new URL("/login", req.url), 303);
  res.headers.append("Set-Cookie", clearedSessionCookie());
  return res;
}
