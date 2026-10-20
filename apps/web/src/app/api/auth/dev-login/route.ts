import { NextResponse } from "next/server";
import { createOwnerSession, sessionCookie, upsertOwner } from "@/server/auth/owner";
import { getDb } from "@/server/db";
import { serverEnv } from "@/server/env";
import { demoMembers } from "@/server/services/demo";

export const runtime = "nodejs";

// LOCAL DEMO ONLY: refused on Vercel, with a real database, outside DEMO mode, or unless APEX_ALLOW_LOCAL_DEV_LOGIN=1.
export async function POST(req: Request) {
  const env = serverEnv();
  if (!env.allowLocalDevLogin) return NextResponse.json({ error: { code: "DISABLED", message: "dev login disabled" } }, { status: 404 });
  if (req.headers.get("sec-fetch-site") && req.headers.get("sec-fetch-site") !== "same-origin") {
    return NextResponse.json({ error: { code: "CSRF", message: "cross-site request refused" } }, { status: 403 });
  }
  const db = await getDb();
  const owner = (await demoMembers()).A!;
  const userId = await upsertOwner(db, owner);
  const token = await createOwnerSession(db, userId, "local_dev", req.headers.get("user-agent"));
  const res = NextResponse.redirect(new URL("/", req.url), 303);
  res.headers.append("Set-Cookie", sessionCookie(token));
  return res;
}
